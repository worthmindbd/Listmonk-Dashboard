/**
 * Unsubscribes management page — campaign-grouped view.
 * Click a campaign card → navigate to detail view showing all records.
 */
const Unsubscribes = {
    stats: null,
    imapStatus: null,
    campaignGroups: [],
    settings: { blocklist_enabled: false },
    campaignFilterQuery: '',
    // Detail view state
    activeCampaign: null,
    campaignRecords: { results: [], total: 0 },
    campaignPage: 1,
    campaignPerPage: 50,
    campaignSearchQuery: '',
    campaignSourceFilter: '',
    selectedEmails: new Set(),

    /* ── Helpers ──────────────────────────────────────────── */
    /** Convert YYYY-MM key to readable label, e.g. "March 2026" */
    formatCampaignDate(key) {
        if (!key) return 'Unknown';
        if (key === 'unknown') return 'Unattributed';
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
        if (!name || name === 'Unknown') return 'No matching campaign';
        // Strip leading YYYYMMDD or YYYY-MM-DD prefix with separator
        let cleaned = name.replace(/^\d{4}-?\d{2}-?\d{2}[\s\-_:]*/, '').trim();
        return cleaned || name;
    },

    /** Render a source badge for a log record. */
    renderSourceBadge(record) {
        const source = record.source || 'email';
        if (source === 'link') {
            return '<span class="badge badge-primary" title="Clicked unsubscribe link">link click</span>';
        }
        const kw = App.escapeHtml(record.keyword || 'email reply');
        return `<span class="badge badge-warning" title="${kw}">email reply</span>`;
    },

    async render() {
        const gen = App.beginRender();
        App.setContent('<div class="loading-spinner">Loading unsubscribes...</div>');

        try {
            const [statsRes, statusRes, groupsRes, settingsRes] = await Promise.allSettled([
                API.get('/api/unsubscribes/stats'),
                API.get('/api/unsubscribes/imap-status'),
                API.get('/api/unsubscribes/campaigns'),
                API.get('/api/unsubscribes/settings'),
            ]);
            if (!App.isRenderCurrent(gen)) return; // navigated away mid-load

            this.stats = statsRes.status === 'fulfilled' ? statsRes.value : { total: 0, today: 0, this_week: 0 };
            this.imapStatus = statusRes.status === 'fulfilled' ? statusRes.value : { configured: false, connected: false };
            this.campaignGroups = groupsRes.status === 'fulfilled' ? (groupsRes.value.data || []) : [];
            this.settings = settingsRes.status === 'fulfilled' ? settingsRes.value : { blocklist_enabled: false };

            // If we're in detail view, stay there and re-sync active campaign
            if (this.activeCampaign) {
                const fresh = this.campaignGroups.find(g => String(g.campaign_id) === String(this.activeCampaign.campaign_id));
                if (fresh) this.activeCampaign = fresh;
                await this.loadCampaignRecords(this.activeCampaign.campaign_id);
                if (!App.isRenderCurrent(gen)) return;
                this.renderDetailView();
            } else {
                this.renderListView();
            }
        } catch (err) {
            if (!App.isRenderCurrent(gen)) return;
            App.setContent(`<div class="empty-state"><h3>Failed to load unsubscribes</h3><p>${App.escapeHtml(err.message || '')}</p></div>`);
        }
    },

    /* ── List View — shows campaign cards ──────────────────── */
    renderListView() {
        this.activeCampaign = null;
        this.selectedEmails.clear();

        const stats = this.stats || { total: 0, today: 0, this_week: 0 };
        const imap = this.imapStatus || { configured: false, connected: false };
        const groups = this.campaignGroups || [];
        const hasRecords = Boolean(stats.total && stats.total > 0);

        // IMAP badge
        let imapBadge;
        if (!imap.configured) {
            imapBadge = '<span class="badge badge-default" style="font-size:0.85rem;padding:6px 14px">IMAP Not Configured</span>';
        } else if (imap.connected) {
            imapBadge = '<span class="badge badge-success" style="font-size:0.85rem;padding:6px 14px">IMAP Connected</span>';
        } else {
            const errDetail = App.escapeHtml(imap.error || 'Unknown');
            imapBadge = `<span class="badge badge-danger" style="font-size:0.85rem;padding:6px 14px" title="${errDetail}">IMAP Error: ${errDetail}</span>`;
        }

        // Top bar actions
        App.setActions(`
            <button class="btn btn-sm" id="unsubExportAllBtn" onclick="Unsubscribes.exportAllCSV()" ${!hasRecords ? 'disabled' : ''}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Export All
            </button>
            <button class="btn btn-sm" id="unsubUndoBtn" onclick="Unsubscribes.resetAll()" ${!hasRecords ? 'disabled' : ''} title="Undo all unsubscribes and restore list memberships">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><polyline points="3 3 3 8 8 8"/></svg>
                Undo All
            </button>
            <button class="btn btn-sm btn-primary" id="unsubScanBtn" onclick="Unsubscribes.triggerScan()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                Scan Now
            </button>
        `);

        // Filter groups by user query
        const q = (this.campaignFilterQuery || '').toLowerCase().trim();
        const filteredGroups = q
            ? groups.filter(g => {
                const name = (g.campaign_name || '').toLowerCase();
                const key = (g.campaign_key || '').toLowerCase();
                const dateLabel = this.formatCampaignDate(g.campaign_key).toLowerCase();
                return name.includes(q) || key.includes(q) || dateLabel.includes(q);
            })
            : groups;

        let html = `
            <!-- IMAP Status -->
            <div class="card" style="margin-bottom:20px">
                <div class="card-header">
                    <h3 class="card-title">IMAP Unsubscribe Monitor</h3>
                    <div class="action-btns">${imapBadge}</div>
                </div>
                <div class="unsub-monitor-info">
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
                        <span style="color:var(--text-muted)">Scans:</span>
                        <strong>Reply text only</strong>
                        <span style="color:var(--text-muted);font-size:0.75rem">(excludes quoted content)</span>
                    </div>
                    <div class="unsub-action-toggle">
                        <span style="color:var(--text-muted)">Action:</span>
                        <strong id="unsubActionLabel">${this.settings.blocklist_enabled ? 'Unsubscribe + Blocklist' : 'Unsubscribe Only'}</strong>
                        <label class="toggle-switch" style="margin-left:4px" title="Toggle blocklist: when ON, leads are also blocklisted">
                            <input type="checkbox" id="unsubBlocklistToggle" ${this.settings.blocklist_enabled ? 'checked' : ''} onchange="Unsubscribes.toggleBlocklist(this.checked)">
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div>
                        <span style="color:var(--text-muted)">Sources:</span>
                        <strong>${App.formatNumber(stats.email_count || 0)} email repl${(stats.email_count || 0) !== 1 ? 'ies' : 'y'}</strong>
                        <span style="color:var(--text-muted);margin:0 6px">·</span>
                        <strong>${App.formatNumber(stats.link_count || 0)} link click${(stats.link_count || 0) !== 1 ? 's' : ''}</strong>
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

        if (groups.length > 0) {
            html += `
                <div class="search-bar" style="margin-top:16px;margin-bottom:16px">
                    <div class="search-input">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                        <input type="search" id="unsubCampaignSearch" placeholder="Filter campaigns by name or date..."
                            value="${App.escapeHtml(this.campaignFilterQuery)}"
                            oninput="Unsubscribes.filterCampaigns(this.value)">
                    </div>
                    ${this.campaignFilterQuery ? '<button class="btn btn-secondary" onclick="Unsubscribes.clearCampaignFilter()">Clear</button>' : ''}
                </div>`;
        }

        if (!groups.length) {
            html += `
                <div class="empty-state" style="padding:40px">
                    <h3>No unsubscribe campaigns detected</h3>
                    <p>The system monitors your inbox every hour. Unsubscribes will be grouped by campaign month.</p>
                </div>`;
        } else if (!filteredGroups.length) {
            html += `
                <div class="empty-state" style="padding:30px">
                    <p>No campaigns match "${App.escapeHtml(this.campaignFilterQuery)}"</p>
                </div>`;
        } else {
            html += '<div class="unsub-campaigns-list">';
            filteredGroups.forEach(g => {
                const readableDate = this.formatCampaignDate(g.campaign_key);
                const cleanName = App.escapeHtml(this.cleanCampaignName(g.campaign_name));
                const cidArg = App.escapeHtml(JSON.stringify(g.campaign_id));
                html += `
                    <div class="unsub-campaign-card" onclick="Unsubscribes.openCampaign(${cidArg})">
                        <div class="unsub-campaign-header">
                            <div class="unsub-campaign-info">
                                <span class="badge badge-primary" style="font-size:0.8rem;padding:4px 10px;margin-right:8px">${readableDate}</span>
                                <strong>${cleanName}</strong>
                                ${g.campaign_deleted ? '<span class="badge badge-warning" style="margin-left:8px" title="This campaign was deleted in ListMonk; its unsubscribe records are kept as an audit trail">Campaign deleted</span>' : ''}
                            </div>
                            <div class="unsub-campaign-meta">
                                <span class="badge badge-default" style="font-size:0.8rem;text-transform:uppercase">${g.count} unsubscribe${g.count !== 1 ? 's' : ''}</span>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" style="margin-left:8px;color:var(--text-muted)"><polyline points="9 18 15 12 9 6"/></svg>
                            </div>
                        </div>
                    </div>`;
            });
            html += '</div>';
        }

        html += '</div>';
        App.setContent(html);
    },

    filterCampaigns(query) {
        this.campaignFilterQuery = query || '';
        this.renderListView();
        const input = document.getElementById('unsubCampaignSearch');
        if (input) {
            input.focus();
            input.selectionStart = input.selectionEnd = input.value.length;
        }
    },

    clearCampaignFilter() {
        this.campaignFilterQuery = '';
        this.renderListView();
    },

    /* ── Detail View — shows records for a single campaign ── */
    async openCampaign(campaignId) {
        const gen = App.beginRender();
        this.activeCampaign = this.campaignGroups.find(g => String(g.campaign_id) === String(campaignId))
            || { campaign_id: campaignId, campaign_key: 'unknown', campaign_name: 'No matching campaign', count: 0 };
        this.campaignPage = 1;
        this.campaignSearchQuery = '';
        this.campaignSourceFilter = '';
        this.selectedEmails.clear();

        App.setContent('<div class="loading-spinner">Loading campaign records...</div>');

        await this.loadCampaignRecords(campaignId);
        if (!App.isRenderCurrent(gen)) return; // navigated away mid-load
        this.renderDetailView();
    },

    async loadCampaignRecords(campaignId) {
        try {
            const cid = encodeURIComponent(String(campaignId));
            const queryParam = this.campaignSearchQuery ? `&query=${encodeURIComponent(this.campaignSearchQuery)}` : '';
            const sourceParam = this.campaignSourceFilter ? `&source=${encodeURIComponent(this.campaignSourceFilter)}` : '';
            const url = `/api/unsubscribes/campaign/${cid}?page=${this.campaignPage}&per_page=${this.campaignPerPage}${queryParam}${sourceParam}`;
            const res = await API.get(url);
            this.campaignRecords = res.data || { results: [], total: 0 };

            // Clamp page if deletions or search caused current page to exceed max page
            const total = this.campaignRecords.total || 0;
            const maxPage = Math.max(1, Math.ceil(total / this.campaignPerPage));
            if (this.campaignPage > maxPage && total > 0) {
                this.campaignPage = maxPage;
                const retryUrl = `/api/unsubscribes/campaign/${cid}?page=${this.campaignPage}&per_page=${this.campaignPerPage}${queryParam}${sourceParam}`;
                const retryRes = await API.get(retryUrl);
                this.campaignRecords = retryRes.data || { results: [], total: 0 };
            }
        } catch {
            this.campaignRecords = { results: [], total: 0 };
        }
    },

    renderDetailView() {
        const camp = this.activeCampaign || { campaign_id: 'unknown', campaign_key: 'unknown', campaign_name: 'No matching campaign' };
        const data = this.campaignRecords;
        const records = data.results || [];
        const total = data.total || 0;
        const linkCount = data.link_count || 0;
        const emailCount = data.email_count || 0;
        const hasSelected = this.selectedEmails.size > 0;
        const readableDate = this.formatCampaignDate(camp.campaign_key);
        const cleanName = App.escapeHtml(this.cleanCampaignName(camp.campaign_name));

        // Top bar actions
        App.setActions(`
            <button class="btn btn-sm ${hasSelected ? 'btn-danger' : ''}" id="unsubBulkDeleteBtn" onclick="Unsubscribes.bulkDeleteSelected()" ${!hasSelected ? 'disabled' : ''}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                Delete Selected${hasSelected ? ` (${this.selectedEmails.size})` : ''}
            </button>
            <button class="btn btn-sm" onclick="Unsubscribes.exportCampaignCSV()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Export CSV
            </button>
            <button class="btn btn-sm btn-danger" onclick="Unsubscribes.removeCampaign()">
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
                    </div>
                </div>
            </div>

            <!-- Campaign stats -->
            <div class="stats-grid" style="margin-bottom:20px">
                <div class="stat-card accent">
                    <div class="stat-label">Total Unsubscribes</div>
                    <div class="stat-value">${App.formatNumber(data.total_unfiltered ?? total)}</div>
                </div>
                <div class="stat-card warning">
                    <div class="stat-label">Email Replies</div>
                    <div class="stat-value">${App.formatNumber(emailCount)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Link Clicks</div>
                    <div class="stat-value">${App.formatNumber(linkCount)}</div>
                </div>
            </div>

            <!-- Records table card -->
            <div class="card">
                <div class="search-bar" style="margin-bottom:16px">
                    <div class="search-input">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                        <input type="search" id="unsubRecordSearch" placeholder="Search by email, name, or keyword..."
                            value="${App.escapeHtml(this.campaignSearchQuery)}"
                            onkeydown="if(event.key==='Enter')Unsubscribes.searchRecords()">
                    </div>
                    <select id="unsubSourceFilter" onchange="Unsubscribes.filterSource(this.value)" style="width:auto">
                        <option value="" ${!this.campaignSourceFilter ? 'selected' : ''}>All Sources</option>
                        <option value="email" ${this.campaignSourceFilter === 'email' ? 'selected' : ''}>Email Replies</option>
                        <option value="link" ${this.campaignSourceFilter === 'link' ? 'selected' : ''}>Link Clicks</option>
                    </select>
                    <button class="btn" onclick="Unsubscribes.searchRecords()">Search</button>
                    ${(this.campaignSearchQuery || this.campaignSourceFilter) ? '<button class="btn btn-secondary" onclick="Unsubscribes.clearDetailSearch()">Clear</button>' : ''}
                </div>

                <div class="table-wrapper"><table id="unsubRecordsTable">
                    <thead><tr>
                        <th style="width:30px"><input type="checkbox" id="unsubSelectAll" onchange="Unsubscribes.toggleSelectAll(this)" ${!records.length ? 'disabled' : ''} ${this._isPageFullySelected() ? 'checked' : ''}/></th>
                        <th>#</th><th>Email</th><th>Name</th><th>Type</th><th>Date</th><th>Actions</th>
                    </tr></thead><tbody>`;

        if (!records.length) {
            html += '<tr><td colspan="7"><div class="empty-state"><p>No records found</p></div></td></tr>';
        }

        records.forEach((r, i) => {
            const idx = (this.campaignPage - 1) * this.campaignPerPage + i + 1;
            const email = App.escapeHtml(r.email || '-');
            const name = App.escapeHtml(r.name || '-');
            const isChecked = this.selectedEmails.has(r.email);

            html += `<tr>
                <td><input type="checkbox" value="${email}" onchange="Unsubscribes.toggleSelect(${i})" ${isChecked ? 'checked' : ''}/></td>
                <td>${idx}</td>
                <td><strong>${email}</strong></td>
                <td>${name}</td>
                <td>${Unsubscribes.renderSourceBadge(r)}</td>
                <td>${App.formatDateTime(r.timestamp)}</td>
                <td>
                    <button class="btn btn-sm btn-danger" style="padding:2px 8px;font-size:0.75rem" onclick="Unsubscribes.deleteRecord(${i})">
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

    /* ── Search in Detail View ────────────────────────────── */
    async searchRecords() {
        const input = document.getElementById('unsubRecordSearch');
        this.campaignSearchQuery = (input ? input.value : '').trim();
        this.campaignPage = 1;
        await this.loadCampaignRecords(this.activeCampaign.campaign_id);
        this.renderDetailView();
    },

    async filterSource(source) {
        this.campaignSourceFilter = source || '';
        this.campaignPage = 1;
        await this.loadCampaignRecords(this.activeCampaign.campaign_id);
        this.renderDetailView();
    },

    async clearDetailSearch() {
        this.campaignSearchQuery = '';
        this.campaignSourceFilter = '';
        this.campaignPage = 1;
        await this.loadCampaignRecords(this.activeCampaign.campaign_id);
        this.renderDetailView();
    },

    /* ── Navigation ───────────────────────────────────────── */
    backToList() {
        this.activeCampaign = null;
        this.selectedEmails.clear();
        this.campaignSearchQuery = '';
        this.campaignSourceFilter = '';
        this.campaignPage = 1;
        this.renderListView();
    },

    /* ── Selection ────────────────────────────────────────── */
    /** Emails selected on the *current* page (the set also spans pages). */
    _pageEmails() {
        return (this.campaignRecords.results || []).map(r => r.email).filter(Boolean);
    },

    /** True only when every row on this page is selected. */
    _isPageFullySelected() {
        const emails = this._pageEmails();
        return emails.length > 0 && emails.every(e => this.selectedEmails.has(e));
    },

    _updateSelectionUI() {
        const count = this.selectedEmails.size;
        const hasSelected = count > 0;
        const deleteBtn = document.getElementById('unsubBulkDeleteBtn');
        if (deleteBtn) {
            deleteBtn.disabled = !hasSelected;
            deleteBtn.classList.toggle('btn-danger', hasSelected);
            deleteBtn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                Delete Selected${hasSelected ? ` (${count})` : ''}
            `;
        }
        const selectAllBox = document.getElementById('unsubSelectAll');
        if (selectAllBox) {
            selectAllBox.checked = this._isPageFullySelected();
        }
    },

    toggleSelect(idx) {
        const records = this.campaignRecords.results || [];
        const email = records[idx]?.email;
        if (!email) return;
        if (this.selectedEmails.has(email)) {
            this.selectedEmails.delete(email);
        } else {
            this.selectedEmails.add(email);
        }
        this._updateSelectionUI();
    },

    toggleSelectAll(checkbox) {
        const emails = this._pageEmails();
        if (checkbox.checked) {
            emails.forEach(e => this.selectedEmails.add(e));
        } else {
            emails.forEach(e => this.selectedEmails.delete(e));
        }
        const rowBoxes = document.querySelectorAll('#unsubRecordsTable tbody input[type="checkbox"]');
        rowBoxes.forEach(box => {
            box.checked = this.selectedEmails.has(box.value);
        });
        this._updateSelectionUI();
    },

    /* ── Pagination ───────────────────────────────────────── */
    async goToCampaignPage(p) {
        const gen = App.beginRender();
        this.campaignPage = p;
        await this.loadCampaignRecords(this.activeCampaign.campaign_id);
        if (!App.isRenderCurrent(gen)) return; // stale pagination (rapid page clicks)
        this.renderDetailView();
    },

    /* ── CRUD Actions ─────────────────────────────────────── */
    async deleteRecord(idx) {
        const records = this.campaignRecords.results || [];
        const email = records[idx]?.email;
        if (!email) return;
        if (!await App.confirm('Remove Record',
                `Remove "${email}" from this campaign's unsubscribe list?`)) return;
        try {
            const campParam = this.activeCampaign ? `&campaign_id=${encodeURIComponent(String(this.activeCampaign.campaign_id))}` : '';
            await API.del(`/api/unsubscribes/records?emails=${encodeURIComponent(email)}${campParam}`);
            App.toast(`Removed ${email}`, 'success');
            this.selectedEmails.delete(email);
            await this.render();
        } catch {
            // API.request already surfaced the server message.
        }
    },

    async bulkDeleteSelected() {
        const count = this.selectedEmails.size;
        if (!count) return;
        if (!await App.confirm('Remove Records',
                `Remove ${count} selected record(s)? This cannot be undone.`)) return;
        try {
            const emailParams = [...this.selectedEmails].map(e => `emails=${encodeURIComponent(e)}`).join('&');
            const campParam = this.activeCampaign ? `&campaign_id=${encodeURIComponent(String(this.activeCampaign.campaign_id))}` : '';
            await API.del(`/api/unsubscribes/records?${emailParams}${campParam}`);
            App.toast(`Removed ${count} record(s)`, 'success');
            this.selectedEmails.clear();
            await this.render();
        } catch {
            // API.request already surfaced the server message.
        }
    },

    async removeCampaign(campaignId) {
        const cid = campaignId !== undefined ? campaignId : this.activeCampaign?.campaign_id;
        if (cid === undefined || cid === null) return;
        if (!await App.confirm('Remove Campaign Records',
                'Remove ALL unsubscribe records for this campaign? This cannot be undone.')) return;
        try {
            const res = await API.del(`/api/unsubscribes/campaign/${encodeURIComponent(String(cid))}`);
            App.toast(res.message || 'Campaign removed', 'success');
            this.activeCampaign = null;
            await this.render();
        } catch {
            // API.request already surfaced the server message.
        }
    },

    /* ── Export ────────────────────────────────────────────── */
    async exportCampaignCSV(campaignId) {
        const cid = campaignId !== undefined ? campaignId : this.activeCampaign?.campaign_id;
        if (cid === undefined || cid === null) return;
        try {
            const result = await API.get(`/api/unsubscribes/campaign/${encodeURIComponent(String(cid))}/export`);
            if (result.blob) {
                API.downloadBlob(result.blob, `unsubscribes_campaign_${cid}.csv`);
                App.toast('Campaign exported', 'success');
            }
        } catch {
            // API.request already surfaced the server message.
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
            // API.request already surfaced the server message (404 when empty).
        }
    },

    /* ── Scan ─────────────────────────────────────────────── */
    async triggerScan() {
        const scanBtn = document.getElementById('unsubScanBtn');
        if (scanBtn) {
            scanBtn.disabled = true;
            scanBtn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px;animation:spin 1s linear infinite">
                    <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                </svg>
                Scanning...`;
        }
        App.toast('Scanning inbox and lists...', 'info');
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 60000);
            const resp = await fetch('/api/unsubscribes/scan', {
                method: 'POST',
                signal: controller.signal,
            });
            clearTimeout(timeout);
            if (resp.status === 401) {
                window.location.href = '/auth/login';
                return;
            }
            if (!resp.ok) {
                let detail = `Scan failed (HTTP ${resp.status})`;
                try {
                    const errBody = await resp.json();
                    if (errBody?.detail) detail = errBody.detail;
                } catch { /* non-JSON body */ }
                App.toast(detail, 'error');
                return;
            }
            const result = await resp.json();
            const imapData = result.imap || result;
            const linkData = result.link || {};
            const totalProcessed = (imapData.processed || 0) + (linkData.processed || 0);
            const msg = `Scanned ${imapData.scanned || 0} emails + ${linkData.scanned_lists || 0} lists — ${totalProcessed} unsubscribed`;
            App.toast(msg, totalProcessed > 0 ? 'success' : 'info');
            await this.render();
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

    /* ── Settings ─────────────────────────────────────────── */
    async toggleBlocklist(enabled) {
        const toggle = document.getElementById('unsubBlocklistToggle');
        const label = document.getElementById('unsubActionLabel');
        try {
            await API.put('/api/unsubscribes/settings', { blocklist_enabled: enabled });
            this.settings.blocklist_enabled = enabled;
            if (label) label.textContent = enabled ? 'Unsubscribe + Blocklist' : 'Unsubscribe Only';
            App.toast(`Action set to: ${enabled ? 'Unsubscribe + Blocklist' : 'Unsubscribe Only'}`, 'success');
        } catch {
            this.settings.blocklist_enabled = !enabled;
            if (toggle) toggle.checked = !enabled;
            if (label) label.textContent = !enabled ? 'Unsubscribe + Blocklist' : 'Unsubscribe Only';
        }
    },

    async resetAll() {
        if (!await App.confirm('Undo All Unsubscribes',
            'This will UNDO all unsubscribes — re-enable and re-subscribe all processed leads in ListMonk, then remove restored records from the log. Continue?')) return;

        const undoBtn = document.getElementById('unsubUndoBtn');
        if (undoBtn) {
            undoBtn.disabled = true;
            undoBtn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px;animation:spin 1s linear infinite"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><polyline points="3 3 3 8 8 8"/></svg>
                Resetting...`;
        }
        App.toast('Resetting all unsubscribes...', 'info');
        try {
            const result = await API.post('/api/unsubscribes/reset');
            App.toast(result.message || 'Reset complete', (result.failed || 0) > 0 ? 'warning' : 'success');
            await this.render();
        } catch {
            // API.request already surfaced the server message.
        } finally {
            if (undoBtn) {
                undoBtn.disabled = false;
                undoBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right:4px"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><polyline points="3 3 3 8 8 8"/></svg>
                    Undo All`;
            }
        }
    },
};
