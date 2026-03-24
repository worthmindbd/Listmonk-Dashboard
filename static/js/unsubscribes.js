/**
 * Unsubscribes management page — campaign-grouped view.
 * Click a campaign card → navigate to detail view showing all records.
 */
const Unsubscribes = {
    stats: null,
    imapStatus: null,
    campaignGroups: [],
    // Detail view state
    activeCampaign: null,
    campaignRecords: { results: [], total: 0 },
    campaignPage: 1,
    campaignPerPage: 50,
    selectedEmails: new Set(),

    /* ── Helpers ──────────────────────────────────────────── */
    /** Convert YYYY-MM key to readable label, e.g. "March 2026" */
    formatCampaignDate(key) {
        if (!key) return 'Unknown';
        const months = ['January','February','March','April','May','June',
                        'July','August','September','October','November','December'];
        const parts = key.split('-');
        if (parts.length === 2) {
            const year = parts[0];
            const monthIdx = parseInt(parts[1], 10) - 1;
            if (monthIdx >= 0 && monthIdx < 12) return `${months[monthIdx]} ${year}`;
        }
        return key;
    },

    /** Strip leading date prefix from raw Listmonk campaign name */
    cleanCampaignName(name) {
        if (!name) return 'Unknown Campaign';
        // Strip leading YYYYMMDD or YYYY-MM-DD prefix
        let cleaned = name.replace(/^\d{4}-?\d{2}-?\d{2}\s*/, '').trim();
        return cleaned || name;
    },

    async render() {
        App.setContent('<div class="loading-spinner">Loading unsubscribes...</div>');

        try {
            const [statsRes, statusRes, groupsRes] = await Promise.allSettled([
                API.get('/api/unsubscribes/stats'),
                API.get('/api/unsubscribes/imap-status'),
                API.get('/api/unsubscribes/campaigns'),
            ]);

            this.stats = statsRes.status === 'fulfilled' ? statsRes.value : { total: 0, today: 0, this_week: 0 };
            this.imapStatus = statusRes.status === 'fulfilled' ? statusRes.value : { configured: false, connected: false };
            this.campaignGroups = groupsRes.status === 'fulfilled' ? (groupsRes.value.data || []) : [];

            // If we're in detail view, stay there
            if (this.activeCampaign) {
                await this.loadCampaignRecords(this.activeCampaign.campaign_key);
                this.renderDetailView();
            } else {
                this.renderListView();
            }
        } catch (err) {
            App.setContent(`<div class="empty-state"><h3>Failed to load unsubscribes</h3><p>${err.message || ''}</p></div>`);
        }
    },

    /* ── List View — shows campaign cards ──────────────────── */
    renderListView() {
        this.activeCampaign = null;
        this.selectedEmails.clear();

        const stats = this.stats;
        const imap = this.imapStatus;
        const groups = this.campaignGroups;

        // IMAP badge
        let imapBadge;
        if (!imap.configured) {
            imapBadge = '<span class="badge badge-default" style="font-size:0.85rem;padding:6px 14px">IMAP Not Configured</span>';
        } else if (imap.connected) {
            imapBadge = '<span class="badge badge-success" style="font-size:0.85rem;padding:6px 14px">IMAP Connected</span>';
        } else {
            imapBadge = `<span class="badge badge-danger" style="font-size:0.85rem;padding:6px 14px">IMAP Error: ${imap.error || 'Unknown'}</span>`;
        }

        // Top bar actions
        App.setActions(`
            <button class="btn btn-sm" onclick="Unsubscribes.exportAllCSV()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Export All
            </button>
            <button class="btn btn-sm btn-primary" onclick="Unsubscribes.triggerScan()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                Scan Now
            </button>
        `);

        let html = `
            <!-- IMAP Status -->
            <div class="card" style="margin-bottom:20px">
                <div class="card-header">
                    <h3 class="card-title">IMAP Unsubscribe Monitor</h3>
                    <div class="action-btns">${imapBadge}</div>
                </div>
                <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;font-size:0.85rem;color:var(--text-secondary)">
                    <div>
                        <span style="color:var(--text-muted)">Scan interval:</span>
                        <strong>Every 1 hour</strong>
                    </div>
                    <div>
                        <span style="color:var(--text-muted)">Keywords:</span>
                        <code style="font-size:0.8rem">"Remove me"</code>,
                        <code style="font-size:0.8rem">"Unsubscribe me"</code>,
                        <code style="font-size:0.8rem">"Exclude me"</code>
                    </div>
                    <div>
                        <span style="color:var(--text-muted)">Action:</span>
                        <strong>Unsubscribe + Blocklist</strong>
                    </div>
                </div>
            </div>

            <!-- Stats -->
            <div class="stats-grid">
                <div class="stat-card accent">
                    <div class="stat-label">Total Unsubscribes</div>
                    <div class="stat-value">${App.formatNumber(stats.total)}</div>
                </div>
                <div class="stat-card warning">
                    <div class="stat-label">Today</div>
                    <div class="stat-value">${App.formatNumber(stats.today)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">This Week</div>
                    <div class="stat-value">${App.formatNumber(stats.this_week)}</div>
                </div>
            </div>

            <!-- Campaign Groups -->
            <div class="card" style="margin-top:20px">
                <div class="card-header">
                    <h3 class="card-title">Unsubscribes by Campaign</h3>
                    <span style="color:var(--text-muted);font-size:0.85rem">${groups.length} campaign(s)</span>
                </div>`;

        if (!groups.length) {
            html += `
                <div class="empty-state" style="padding:40px">
                    <h3>No unsubscribe campaigns detected</h3>
                    <p>The system monitors your inbox every hour. Unsubscribes will be grouped by campaign month.</p>
                </div>`;
        } else {
            html += '<div class="unsub-campaigns-list">';
            groups.forEach(g => {
                const readableDate = this.formatCampaignDate(g.campaign_key);
                const cleanName = this.cleanCampaignName(g.campaign_name).replace(/</g, '&lt;');
                html += `
                    <div class="unsub-campaign-card" onclick="Unsubscribes.openCampaign('${g.campaign_key}')">
                        <div class="unsub-campaign-header">
                            <div class="unsub-campaign-info">
                                <span class="badge badge-primary" style="font-size:0.8rem;padding:4px 10px;margin-right:8px">${readableDate}</span>
                                <strong>${cleanName}</strong>
                            </div>
                            <div class="unsub-campaign-meta">
                                <span class="badge badge-default" style="font-size:0.8rem;text-transform:uppercase">${g.count} unsubscribe${g.count !== 1 ? 's' : ''}</span>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" style="margin-left:8px;color:var(--text-muted)">
                                    <polyline points="9 18 15 12 9 6"/>
                                </svg>
                            </div>
                        </div>
                    </div>`;
            });
            html += '</div>';
        }

        html += '</div>';
        App.setContent(html);
    },

    /* ── Detail View — shows records for a single campaign ── */
    async openCampaign(key) {
        // Find the campaign group info
        this.activeCampaign = this.campaignGroups.find(g => g.campaign_key === key) || { campaign_key: key, campaign_name: 'Unknown', count: 0 };
        this.campaignPage = 1;
        this.selectedEmails.clear();

        App.setContent('<div class="loading-spinner">Loading campaign records...</div>');

        await this.loadCampaignRecords(key);
        this.renderDetailView();
    },

    async loadCampaignRecords(key) {
        try {
            const res = await API.get(`/api/unsubscribes/campaign/${encodeURIComponent(key)}?page=${this.campaignPage}&per_page=${this.campaignPerPage}`);
            this.campaignRecords = res.data || { results: [], total: 0 };
        } catch {
            this.campaignRecords = { results: [], total: 0 };
        }
    },

    renderDetailView() {
        const camp = this.activeCampaign;
        const data = this.campaignRecords;
        const records = data.results || [];
        const total = data.total || 0;
        const hasSelected = this.selectedEmails.size > 0;
        const readableDate = this.formatCampaignDate(camp.campaign_key);
        const cleanName = this.cleanCampaignName(camp.campaign_name).replace(/</g, '&lt;');

        // Top bar actions
        App.setActions(`
            <button class="btn btn-sm ${hasSelected ? 'btn-danger' : ''}" onclick="Unsubscribes.bulkDeleteSelected()" ${!hasSelected ? 'disabled' : ''}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                Delete Selected${hasSelected ? ` (${this.selectedEmails.size})` : ''}
            </button>
            <button class="btn btn-sm" onclick="Unsubscribes.exportCampaignCSV('${camp.campaign_key}')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Export CSV
            </button>
            <button class="btn btn-sm btn-danger" onclick="Unsubscribes.removeCampaign('${camp.campaign_key}')">
                Remove List
            </button>
        `);

        let html = `
            <!-- Back button + header -->
            <div style="margin-bottom:20px">
                <button class="btn btn-sm" onclick="Unsubscribes.backToList()" style="margin-bottom:12px">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
                    Back to Campaigns
                </button>
                <div class="card">
                    <div class="card-header" style="margin-bottom:0">
                        <div style="display:flex;align-items:center;gap:10px">
                            <span class="badge badge-primary" style="font-size:0.9rem;padding:6px 14px">${readableDate}</span>
                            <h3 class="card-title" style="margin:0">${cleanName}</h3>
                        </div>
                        <span style="color:var(--text-muted);font-size:0.85rem">${total} record${total !== 1 ? 's' : ''}</span>
                    </div>
                </div>
            </div>

            <!-- Records table -->
            <div class="card">
                <div class="table-wrapper"><table>
                    <thead><tr>
                        <th style="width:30px"><input type="checkbox" onchange="Unsubscribes.toggleSelectAll(this)" ${this.selectedEmails.size === records.length && records.length > 0 ? 'checked' : ''}/></th>
                        <th>#</th><th>Email</th><th>Name</th><th>Keyword</th><th>Date</th><th>Actions</th>
                    </tr></thead><tbody>`;

        if (!records.length) {
            html += '<tr><td colspan="7"><div class="empty-state"><p>No records</p></div></td></tr>';
        }

        records.forEach((r, i) => {
            const idx = (this.campaignPage - 1) * this.campaignPerPage + i + 1;
            const email = (r.email || '-').replace(/</g, '&lt;');
            const name = (r.name || '-').replace(/</g, '&lt;');
            const keyword = (r.keyword || '-').replace(/</g, '&lt;');
            const isChecked = this.selectedEmails.has(r.email);

            html += `<tr>
                <td><input type="checkbox" value="${email}" onchange="Unsubscribes.toggleSelect('${r.email}')" ${isChecked ? 'checked' : ''}/></td>
                <td>${idx}</td>
                <td><strong>${email}</strong></td>
                <td>${name}</td>
                <td><span class="badge badge-warning">${keyword}</span></td>
                <td>${App.formatDateTime(r.timestamp)}</td>
                <td>
                    <button class="btn btn-sm btn-danger" style="padding:2px 8px;font-size:0.75rem" onclick="Unsubscribes.deleteRecord('${r.email}')">
                        Remove
                    </button>
                </td>
            </tr>`;
        });

        html += '</tbody></table></div>';
        html += App.renderPagination(this.campaignPage, total, this.campaignPerPage, 'Unsubscribes.goToCampaignPage');
        html += '</div>';

        App.setContent(html);
    },

    /* ── Navigation ───────────────────────────────────────── */
    backToList() {
        this.activeCampaign = null;
        this.selectedEmails.clear();
        this.renderListView();
    },

    /* ── Selection ────────────────────────────────────────── */
    toggleSelect(email) {
        if (this.selectedEmails.has(email)) {
            this.selectedEmails.delete(email);
        } else {
            this.selectedEmails.add(email);
        }
        this.renderDetailView();
    },

    toggleSelectAll(checkbox) {
        const records = this.campaignRecords.results || [];
        if (checkbox.checked) {
            records.forEach(r => this.selectedEmails.add(r.email));
        } else {
            this.selectedEmails.clear();
        }
        this.renderDetailView();
    },

    /* ── Pagination ───────────────────────────────────────── */
    async goToCampaignPage(p) {
        this.campaignPage = p;
        await this.loadCampaignRecords(this.activeCampaign.campaign_key);
        this.renderDetailView();
    },

    /* ── CRUD Actions ─────────────────────────────────────── */
    async deleteRecord(email) {
        if (!confirm(`Remove "${email}" from this campaign's unsubscribe list?`)) return;
        try {
            await API.post('/api/unsubscribes/records/delete', { emails: [email] });
            App.toast(`Removed ${email}`, 'success');
            this.selectedEmails.delete(email);
            await this.render();
        } catch {
            App.toast('Failed to remove record', 'error');
        }
    },

    async bulkDeleteSelected() {
        const count = this.selectedEmails.size;
        if (!count) return;
        if (!confirm(`Remove ${count} selected record(s)? This cannot be undone.`)) return;
        try {
            await API.post('/api/unsubscribes/records/delete', { emails: [...this.selectedEmails] });
            App.toast(`Removed ${count} record(s)`, 'success');
            this.selectedEmails.clear();
            await this.render();
        } catch {
            App.toast('Failed to remove records', 'error');
        }
    },

    async removeCampaign(key) {
        if (!confirm(`Remove ALL unsubscribe records for campaign "${key}"? This cannot be undone.`)) return;
        try {
            const res = await API.del(`/api/unsubscribes/campaign/${encodeURIComponent(key)}`);
            App.toast(res.message || 'Campaign removed', 'success');
            this.activeCampaign = null;
            await this.render();
        } catch {
            App.toast('Failed to remove campaign', 'error');
        }
    },

    /* ── Export ────────────────────────────────────────────── */
    async exportCampaignCSV(key) {
        try {
            const result = await API.get(`/api/unsubscribes/campaign/${encodeURIComponent(key)}/export`);
            if (result.blob) {
                API.downloadBlob(result.blob, `unsubscribes_${key.replace('/', '-')}.csv`);
                App.toast('Campaign exported', 'success');
            }
        } catch {
            App.toast('Export failed', 'error');
        }
    },

    async exportAllCSV() {
        try {
            const result = await API.get('/api/unsubscribes/export');
            if (result.blob) {
                API.downloadBlob(result.blob, 'unsubscribes_all.csv');
                App.toast('All unsubscribes exported', 'success');
            }
        } catch {
            App.toast('Export failed — no records found', 'error');
        }
    },

    /* ── Scan ─────────────────────────────────────────────── */
    async triggerScan() {
        // Disable button while scanning
        const scanBtn = document.querySelector('[onclick="Unsubscribes.triggerScan()"]');
        if (scanBtn) {
            scanBtn.disabled = true;
            scanBtn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px;animation:spin 1s linear infinite">
                    <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                </svg>
                Scanning...`;
        }
        App.toast('Scanning inbox...', 'info');
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 60000);
            const resp = await fetch('/api/unsubscribes/scan', {
                method: 'POST',
                signal: controller.signal,
            });
            clearTimeout(timeout);
            const result = await resp.json();
            if (result.error) {
                App.toast(result.error, 'error');
            } else {
                const msg = `Scanned ${result.scanned || 0} emails — ${result.processed || 0} unsubscribed`;
                App.toast(msg, result.processed > 0 ? 'success' : 'info');
                await this.render();
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                App.toast('Scan timed out — try again later', 'error');
            } else {
                App.toast(`Scan failed: ${err.message || 'Unknown error'}`, 'error');
            }
        } finally {
            if (scanBtn) {
                scanBtn.disabled = false;
                scanBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px">
                        <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                    </svg>
                    Scan Now`;
            }
        }
    },
};
