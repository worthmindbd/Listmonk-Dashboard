/**
 * Main SPA router and shared UI components.
 */
const App = {
    currentPage: null,

    pages: {
        'dashboard': { title: 'Dashboard', render: () => Dashboard.render() },
        'subscribers': { title: 'Subscribers', render: () => Subscribers.render() },
        'lists': { title: 'Lists', render: () => Lists.render() },
        'campaigns': { title: 'Campaigns', render: () => Campaigns.render() },
        'analytics': { title: 'Analytics', render: () => Analytics.render() },
        'settings': { title: 'Settings', render: () => Settings.render() },
        'templates': { title: 'Templates', render: () => Templates.render() },
        'bounces': { title: 'Bounces', render: () => Bounces.render() },
        'converter': { title: 'CSV Converter', render: () => Converter.render() },
        'unsubscribes': { title: 'Unsubscribes', render: () => Unsubscribes.render() },
    },

    init() {
        window.addEventListener('hashchange', () => this.route());
        document.getElementById('menuToggle').addEventListener('click', () => {
            document.getElementById('sidebar').classList.toggle('open');
        });

        // Close sidebar on content click (mobile)
        document.querySelector('.content-area').addEventListener('click', () => {
            document.getElementById('sidebar').classList.remove('open');
        });

        // Modal handlers
        document.getElementById('modalClose').addEventListener('click', () => this.closeModal());
        document.getElementById('modalCancel').addEventListener('click', () => this.closeModal());
        document.getElementById('modalOverlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) this.closeModal();
        });

        this.checkConnection();
        this.route();
    },

    route() {
        const hash = window.location.hash.slice(2) || 'dashboard';
        const page = this.pages[hash];

        if (!page) {
            window.location.hash = '#/dashboard';
            return;
        }

        this.currentPage = hash;

        // Update nav
        document.querySelectorAll('.nav-link').forEach(link => {
            link.classList.toggle('active', link.dataset.page === hash);
        });

        // Update title
        document.getElementById('pageTitle').textContent = page.title;
        document.getElementById('topBarActions').innerHTML = '';

        // Render page
        const content = document.getElementById('contentArea');
        content.innerHTML = '<div class="loading-spinner">Loading...</div>';
        page.render();
    },

    async checkConnection() {
        const dot = document.querySelector('.status-dot');
        const text = document.querySelector('.status-text');
        try {
            await API.get('/api/lists?per_page=1&minimal=true');
            dot.className = 'status-dot connected';
            text.textContent = 'Connected';
        } catch {
            dot.className = 'status-dot error';
            text.textContent = 'Disconnected';
        }
    },

    // ── Toast ────────────────────────────────────────────
    toast(message, type = 'info') {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100%)';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    },

    // ── Modal / Confirm ──────────────────────────────────
    _modalResolve: null,

    confirm(title, message) {
        return new Promise((resolve) => {
            this._modalResolve = resolve;
            document.getElementById('modalTitle').textContent = title;
            document.getElementById('modalBody').innerHTML = `<p>${message}</p>`;
            document.getElementById('modalOverlay').style.display = 'flex';
            document.getElementById('modalConfirm').onclick = () => {
                this.closeModal();
                resolve(true);
            };
        });
    },

    closeModal() {
        document.getElementById('modalOverlay').style.display = 'none';
        if (this._modalResolve) {
            this._modalResolve(false);
            this._modalResolve = null;
        }
    },

    // ── Pagination ───────────────────────────────────────
    renderPagination(currentPage, total, perPage, onPageChange) {
        const totalPages = Math.ceil(total / perPage);
        if (totalPages <= 1) return '';

        let html = '<div class="pagination">';
        html += `<button class="btn btn-sm" ${currentPage <= 1 ? 'disabled' : ''} onclick="${onPageChange}(${currentPage - 1})">Prev</button>`;
        html += `<span class="pagination-info">Page ${currentPage} of ${totalPages} (${total} total)</span>`;
        html += `<button class="btn btn-sm" ${currentPage >= totalPages ? 'disabled' : ''} onclick="${onPageChange}(${currentPage + 1})">Next</button>`;
        html += '</div>';
        return html;
    },

    // ── Status Badge ─────────────────────────────────────
    statusBadge(status) {
        const map = {
            'enabled': 'success', 'active': 'success', 'running': 'success', 'finished': 'success',
            'blocklisted': 'danger', 'cancelled': 'danger', 'archived': 'default',
            'draft': 'info', 'scheduled': 'warning', 'paused': 'warning',
        };
        const cls = map[status] || 'default';
        return `<span class="badge badge-${cls}">${status}</span>`;
    },

    // ── Format Date ──────────────────────────────────────
    formatDate(dateStr) {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    },

    formatDateTime(dateStr) {
        if (!dateStr) return '-';
        const d = new Date(dateStr);
        return d.toLocaleString('en-US', {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit'
        });
    },

    // ── Number Format ────────────────────────────────────
    formatNumber(n) {
        if (n === undefined || n === null) return '0';
        return n.toLocaleString();
    },

    // ── Set Content ──────────────────────────────────────
    setContent(html) {
        document.getElementById('contentArea').innerHTML = html;
    },

    setActions(html) {
        document.getElementById('topBarActions').innerHTML = html;
    },
};

// ── Templates Page (simple) ──────────────────────────────
const Templates = {
    _data: [],

    async render() {
        try {
            const result = await API.get('/api/templates');
            this._data = result?.data || [];
            const templates = this._data;

            let html = `
                <div class="search-bar">
                    <span style="color:var(--text-secondary);font-size:0.9rem">${templates.length} template(s)</span>
                    <div style="flex:1"></div>
                    <button class="btn btn-primary" onclick="Templates.showCreate()">+ New Template</button>
                </div>
                <div class="table-wrapper"><table>
                    <thead><tr>
                        <th>ID</th><th>Name</th><th>Type</th><th>Default</th><th>Created</th><th>Updated</th><th>Actions</th>
                    </tr></thead><tbody>`;

            if (!templates.length) {
                html += '<tr><td colspan="7"><div class="empty-state"><h3>No templates found</h3></div></td></tr>';
            }
            templates.forEach(t => {
                const name = (t.name || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                html += `<tr>
                    <td>${t.id}</td>
                    <td><strong>${name}</strong></td>
                    <td><span class="badge badge-info">${t.type || '-'}</span></td>
                    <td>${t.is_default ? '<span class="badge badge-success">Default</span>' : '-'}</td>
                    <td>${App.formatDate(t.created_at)}</td>
                    <td>${App.formatDate(t.updated_at)}</td>
                    <td class="action-btns">
                        <button class="btn btn-sm" onclick="Templates.preview(${t.id})">Preview</button>
                        <button class="btn btn-sm" onclick="Templates.showEdit(${t.id})">Edit</button>
                        ${!t.is_default ? `<button class="btn btn-sm" onclick="Templates.setDefault(${t.id})">Set Default</button>` : ''}
                        <button class="btn btn-sm btn-danger" onclick="Templates.remove(${t.id})">Delete</button>
                    </td>
                </tr>`;
            });

            html += '</tbody></table></div>';
            App.setContent(html);
        } catch (err) {
            console.error('Templates load error:', err);
            App.setContent(`<div class="empty-state"><h3>Failed to load templates</h3><p>${err.message || ''}</p></div>`);
        }
    },

    preview(id) {
        const t = this._data.find(tpl => tpl.id === id);
        if (!t) return;
        App.setContent(`
            <div style="margin-bottom:12px">
                <button class="btn btn-secondary" onclick="Templates.render()">Back to Templates</button>
                <span style="margin-left:12px;font-size:1.1rem;font-weight:600">${(t.name || '').replace(/</g, '&lt;')}</span>
            </div>
            <iframe class="preview-frame" srcdoc="${(t.body || '').replace(/"/g, '&quot;')}"></iframe>
        `);
    },

    showCreate() {
        App.setContent(`
            <div class="inline-form">
                <h3 style="margin-bottom:16px">Create Template</h3>
                <div class="form-group"><label>Name</label><input type="text" id="tplName" placeholder="Template name"></div>
                <div class="form-group">
                    <label>Type</label>
                    <select id="tplType"><option value="campaign">Campaign</option></select>
                </div>
                <div class="form-group"><label>Body (HTML)</label><textarea id="tplBody" rows="15" placeholder="HTML content"></textarea></div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="Templates.create()">Create</button>
                    <button class="btn btn-secondary" onclick="Templates.render()">Cancel</button>
                </div>
            </div>
        `);
    },

    async showEdit(id) {
        const t = this._data.find(tpl => tpl.id === id);
        if (!t) { App.toast('Template not found', 'error'); return; }
        App.setContent(`
            <div class="inline-form">
                <h3 style="margin-bottom:16px">Edit Template #${t.id}</h3>
                <div class="form-group"><label>Name</label><input type="text" id="editTplName" value="${(t.name || '').replace(/"/g, '&quot;')}"></div>
                <div class="form-group"><label>Body (HTML)</label><textarea id="editTplBody" rows="15">${(t.body || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</textarea></div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="Templates.update(${t.id})">Update</button>
                    <button class="btn btn-secondary" onclick="Templates.render()">Cancel</button>
                </div>
            </div>
        `);
    },

    async create() {
        const name = document.getElementById('tplName').value.trim();
        const body = document.getElementById('tplBody').value;
        if (!name) { App.toast('Name is required', 'error'); return; }
        try {
            await API.post('/api/templates', { name, body, type: 'campaign' });
            App.toast('Template created', 'success');
            this.render();
        } catch { /* error already toasted by API */ }
    },

    async update(id) {
        const name = document.getElementById('editTplName').value.trim();
        const body = document.getElementById('editTplBody').value;
        if (!name) { App.toast('Name is required', 'error'); return; }
        try {
            await API.put(`/api/templates/${id}`, { name, body });
            App.toast('Template updated', 'success');
            this.render();
        } catch {}
    },

    async setDefault(id) {
        try {
            await API.put(`/api/templates/${id}/default`);
            App.toast('Default template updated', 'success');
            this.render();
        } catch {}
    },

    async remove(id) {
        if (await App.confirm('Delete Template', 'Are you sure you want to delete this template?')) {
            try {
                await API.del(`/api/templates/${id}`);
                App.toast('Template deleted', 'success');
                this.render();
            } catch {}
        }
    },
};

// ── Bounces Page ─────────────────────────────────────────
const Bounces = {
    page: 1,
    campaignFilter: 0,
    campaigns: [],

    async render() {
        try {
            let params = `page=${this.page}&per_page=25`;
            if (this.campaignFilter) params += `&campaign_id=${this.campaignFilter}`;

            // Fetch campaigns (for filter) and bounces in parallel
            const [campRes, result] = await Promise.all([
                this.campaigns.length
                    ? Promise.resolve(null)
                    : API.get('/api/campaigns?per_page=100&order_by=created_at&order=DESC'),
                API.get(`/api/bounces?${params}`),
            ]);
            if (campRes) this.campaigns = campRes?.data?.results || [];
            const data = result?.data || {};
            const bounces = data.results || [];
            const total = data.total || 0;

            // Build campaign filter options
            const campOptions = this.campaigns.map(c =>
                `<option value="${c.id}" ${c.id === this.campaignFilter ? 'selected' : ''}>${c.name} (${c.status})</option>`
            ).join('');

            // Count by type for the filtered set info
            const filterLabel = this.campaignFilter
                ? this.campaigns.find(c => c.id === this.campaignFilter)?.name || `#${this.campaignFilter}`
                : 'All campaigns';

            App.setActions(`
                <button class="btn btn-sm" onclick="Bounces.fixBlacklistBounces()">Fix Blacklist Bounces</button>
                <button class="btn btn-sm" onclick="Bounces.scanBounceMail()">Scan Bounce Mail</button>
                <button class="btn btn-sm" onclick="Bounces.exportBounces()">Export CSV</button>
                <button class="btn btn-sm btn-danger" onclick="Bounces.deleteAll()">Delete All Bounces</button>
            `);

            let html = `
                <div class="search-bar">
                    <select id="bounceCampFilter" onchange="Bounces.filterCampaign(this.value)" style="width:auto;min-width:250px">
                        <option value="0" ${!this.campaignFilter ? 'selected' : ''}>All Campaigns</option>
                        ${campOptions}
                    </select>
                    <span style="color:var(--text-secondary);font-size:0.9rem">
                        ${App.formatNumber(total)} bounce records
                        ${this.campaignFilter ? ' for ' + filterLabel : ''}
                    </span>
                    <div style="flex:1"></div>
                </div>
                <div class="table-wrapper"><table>
                    <thead><tr>
                        <th>ID</th><th>Email</th><th>Campaign</th><th>Source</th><th>Type</th><th>Date</th><th>Actions</th>
                    </tr></thead><tbody>`;

            if (!bounces.length) {
                html += '<tr><td colspan="7"><div class="empty-state"><h3>No bounces found</h3></div></td></tr>';
            }
            bounces.forEach(b => {
                const campName = (b.campaign?.name || '-').replace(/</g, '&lt;');
                html += `<tr>
                    <td>${b.id}</td>
                    <td><strong>${b.email || '-'}</strong></td>
                    <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis">${campName}</td>
                    <td>${b.source || '-'}</td>
                    <td><span class="badge badge-${b.type === 'hard' ? 'danger' : 'warning'}">${b.type || 'unknown'}</span></td>
                    <td>${App.formatDate(b.created_at)}</td>
                    <td><button class="btn btn-sm btn-danger" onclick="Bounces.remove(${b.id})">Delete</button></td>
                </tr>`;
            });

            html += '</tbody></table></div>';
            html += App.renderPagination(this.page, total, 25, 'Bounces.goToPage');
            App.setContent(html);
        } catch (err) {
            App.setContent(`<div class="empty-state"><h3>Failed to load bounces</h3><p>${err.message || ''}</p></div>`);
        }
    },

    filterCampaign(val) {
        this.campaignFilter = parseInt(val) || 0;
        this.page = 1;
        this.render();
    },

    goToPage(p) { this.page = p; this.render(); },

    async remove(id) {
        await API.del(`/api/bounces/${id}`);
        App.toast('Bounce deleted', 'success');
        this.render();
    },

    async deleteAll() {
        const label = this.campaignFilter
            ? `bounces for "${this.campaigns.find(c => c.id === this.campaignFilter)?.name}"`
            : 'ALL bounce records';
        if (await App.confirm('Delete Bounces', `This will permanently delete ${label}. Continue?`)) {
            await API.del('/api/bounces');
            App.toast('Bounces deleted', 'success');
            this.render();
        }
    },

    async exportBounces() {
        const campParam = this.campaignFilter ? `?campaign_id=${this.campaignFilter}` : '';
        try {
            const result = await API.get(`/api/bounces/export${campParam}`);
            if (result.blob) {
                const suffix = this.campaignFilter
                    ? `_${this.campaigns.find(c => c.id === this.campaignFilter)?.name?.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 30) || this.campaignFilter}`
                    : '_all';
                API.downloadBlob(result.blob, `bounces${suffix}.csv`);
                App.toast('Bounces exported', 'success');
            }
        } catch {
            App.toast('Export failed', 'error');
        }
    },

    async fixBlacklistBounces() {
        if (!await App.confirm('Fix Blacklist Bounces',
            'This will scan all hard bounces, find ones caused by IP blacklisting (Spamhaus etc.), ' +
            'reclassify them as soft bounces, and unblock the affected subscribers. Continue?')) return;
        App.toast('Fixing blacklist bounces... this may take a while', 'info');
        try {
            const result = await API.post('/api/bounce-scanner/fix');
            if (result.error) {
                App.toast(`Error: ${result.error}`, 'error');
                return;
            }
            App.toast(
                `Fixed ${result.fixed || 0} bounces (${result.unique_emails_fixed || 0} emails), ` +
                `${result.subscribers_unblocked || 0} subscribers unblocked`,
                'success'
            );
            this.render();
        } catch (err) {
            App.toast(`Fix failed: ${err.message || ''}`, 'error');
        }
    },

    async scanBounceMail() {
        App.toast('Scanning bounce mailbox...', 'info');
        try {
            const result = await API.post('/api/bounce-scanner/scan');
            if (result.error) {
                App.toast(`Error: ${result.error}`, 'error');
                return;
            }
            App.toast(
                `Scanned ${result.scanned || 0} emails, fixed ${result.fixed || 0} blacklist bounces`,
                'success'
            );
            if (result.fixed > 0) this.render();
        } catch (err) {
            App.toast(`Scan failed: ${err.message || ''}`, 'error');
        }
    },
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => App.init());
