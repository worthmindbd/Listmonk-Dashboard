/**
 * Subscribers page - CRUD, search, import/export.
 */
const Subscribers = {
    page: 1,
    query: '',
    listFilter: 0,
    lists: [],

    async render() {
        const gen = App.beginRender();
        try {
            let params = `page=${this.page}&per_page=50`;
            if (this.query) params += `&search=${encodeURIComponent(this.query)}`;
            if (this.listFilter) params += `&list_id=${this.listFilter}`;
            // A list-filter lookup failure must not blank the whole page;
            // API.request has already surfaced the error.
            const fetchLists = !this.lists.length
                ? API.get('/api/lists?per_page=200&minimal=true').catch(() => null)
                : Promise.resolve(null);
            const [result, listsRes] = await Promise.all([
                API.get(`/api/subscribers?${params}`),
                fetchLists,
            ]);
            if (!App.isRenderCurrent(gen)) return; // stale render (navigated away / re-filtered)
            if (listsRes) this.lists = listsRes?.data?.results || [];
            const data = result?.data || {};
            const subscribers = data.results || [];
            const total = data.total || 0;
            // Deleting the last row on page > 1 used to leave an empty table
            // with no pager (renderPagination returns '' for one page).
            const maxPage = Math.max(1, Math.ceil(total / 50));
            if (this.page > maxPage) {
                this.page = maxPage;
                return this.render();
            }

            App.setActions(`
                <button class="btn btn-sm" onclick="Subscribers.exportAll()">Export CSV</button>
                <button class="btn btn-sm btn-primary" onclick="Subscribers.showCreate()">+ Add Subscriber</button>
            `);

            const listOptions = this.lists.map(l =>
                `<option value="${l.id}" ${l.id === this.listFilter ? 'selected' : ''}>${App.escapeHtml(l.name)}</option>`
            ).join('');

            let html = `
                <div class="search-bar">
                    <select id="subListFilter" onchange="Subscribers.filterList(this.value)"
                            aria-label="Filter subscribers by list" style="width:auto;min-width:180px">
                        <option value="0" ${!this.listFilter ? 'selected' : ''}>All Lists</option>
                        ${listOptions}
                    </select>
                    <div class="search-input">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                        <input type="search" id="subSearch" placeholder="Search subscribers by email or name..."
                            aria-label="Search subscribers by email or name"
                            value="${App.escapeHtml(this.query)}" onkeydown="if(event.key==='Enter')Subscribers.search()">
                    </div>
                    <button class="btn" onclick="Subscribers.search()">Search</button>
                </div>
                <div class="table-wrapper"><table>
                    <thead><tr>
                        <th>ID</th><th>Email</th><th>Name</th><th>Status</th><th>Lists</th><th>Created</th><th class="cell-actions">Actions</th>
                    </tr></thead><tbody>`;

            if (!subscribers.length) {
                html += '<tr><td colspan="7"><div class="empty-state"><h3>No subscribers found</h3></div></td></tr>';
            }

            subscribers.forEach(s => {
                const lists = (s.lists || []).map(l => `<span class="tag">${App.escapeHtml(l.name)}</span>`).join('') || '-';
                html += `<tr>
                    <td>${s.id}</td>
                    <td><strong>${App.escapeHtml(s.email)}</strong></td>
                    <td>${App.escapeHtml(s.name) || '-'}</td>
                    <td>${App.statusBadge(s.status)}</td>
                    <td>${lists}</td>
                    <td>${App.formatDate(s.created_at)}</td>
                    <td class="cell-actions">
                        <div class="action-btns">
                            <button class="btn btn-sm" onclick="Subscribers.showEdit(${s.id})">Edit</button>
                            <button class="btn btn-sm btn-danger" onclick="Subscribers.remove(${s.id})">Delete</button>
                        </div>
                    </td>
                </tr>`;
            });

            html += '</tbody></table></div>';
            html += App.renderPagination(this.page, total, 50, 'Subscribers.goToPage');
            App.setContent(html);
        } catch {
            if (!App.isRenderCurrent(gen)) return;
            App.setContent('<div class="empty-state"><h3>Failed to load subscribers</h3></div>');
        }
    },

    search() {
        this.query = document.getElementById('subSearch')?.value || '';
        this.page = 1;
        this.render();
    },

    filterList(value) {
        this.listFilter = parseInt(value) || 0;
        this.page = 1;
        this.render();
    },

    goToPage(p) { this.page = p; this.render(); },

    showCreate() {
        App.setContent(`
            <div class="inline-form">
                <h3 style="margin-bottom:16px">Add Subscriber</h3>
                <div class="form-grid">
                    <div class="form-group"><label>Email *</label><input type="email" id="subEmail" placeholder="email@example.com"></div>
                    <div class="form-group"><label>Name</label><input type="text" id="subName" placeholder="Full name"></div>
                </div>
                <div class="form-group">
                    <label>Status</label>
                    <select id="subStatus">
                        <option value="enabled">Enabled</option>
                        <option value="disabled">Disabled</option>
                        <option value="blocklisted">Blocklisted</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Attributes (JSON)</label>
                    <textarea id="subAttribs" placeholder='{"company": "Acme"}'>{}</textarea>
                </div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="Subscribers.create()">Create</button>
                    <button class="btn btn-secondary" onclick="Subscribers.render()">Cancel</button>
                </div>
            </div>
        `);
    },

    async create() {
        const email = document.getElementById('subEmail').value.trim();
        const name = document.getElementById('subName').value.trim();
        const status = document.getElementById('subStatus').value;
        let attribs = {};
        try { attribs = JSON.parse(document.getElementById('subAttribs').value); } catch {}

        if (!email) { App.toast('Email is required', 'error'); return; }

        try {
            await API.post('/api/subscribers', { email, name, status, attribs, lists: [] });
            App.toast('Subscriber created', 'success');
            this.render();
        } catch {
            // API.request already surfaced the server message.
        }
    },

    async showEdit(id) {
        const gen = App.beginRender();
        try {
            const result = await API.get(`/api/subscribers/${id}`);
            if (!App.isRenderCurrent(gen)) return;
            const s = result?.data || {};
            // Preserve memberships: ListMonk's PUT clears any list not sent.
            // Keep null when the response omits lists (unknown), so update()
            // lets the backend preserve the current memberships instead of
            // sending [] and wiping them.
            this._lists = Array.isArray(s.lists)
                ? s.lists.filter(l => l && l.id != null).map(l => l.id)
                : null;

            App.setContent(`
                <div class="inline-form">
                    <h3 style="margin-bottom:16px">Edit Subscriber #${s.id}</h3>
                    <div class="form-grid">
                        <div class="form-group"><label>Email</label><input type="email" id="editEmail" value="${App.escapeHtml(s.email || '')}"></div>
                        <div class="form-group"><label>Name</label><input type="text" id="editName" value="${App.escapeHtml(s.name || '')}"></div>
                    </div>
                    <div class="form-group">
                        <label>Status</label>
                        <select id="editStatus">
                            <option value="enabled" ${s.status === 'enabled' ? 'selected' : ''}>Enabled</option>
                            <option value="disabled" ${s.status === 'disabled' ? 'selected' : ''}>Disabled</option>
                            <option value="blocklisted" ${s.status === 'blocklisted' ? 'selected' : ''}>Blocklisted</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Attributes (JSON)</label>
                        <textarea id="editAttribs">${App.escapeHtml(JSON.stringify(s.attribs || {}, null, 2))}</textarea>
                    </div>
                    <div class="form-actions">
                        <button class="btn btn-primary" onclick="Subscribers.update(${s.id})">Update</button>
                        <button class="btn btn-secondary" onclick="Subscribers.render()">Cancel</button>
                    </div>
                </div>
            `);
        } catch {
            // API.request already surfaced the server message.
        }
    },

    async update(id) {
        const email = document.getElementById('editEmail').value.trim();
        const name = document.getElementById('editName').value.trim();
        const status = document.getElementById('editStatus').value;
        let attribs;
        try {
            attribs = JSON.parse(document.getElementById('editAttribs').value || '{}');
        } catch {
            App.toast('Attributes must be valid JSON; nothing was saved', 'error');
            return;
        }
        if (!attribs || typeof attribs !== 'object' || Array.isArray(attribs)) {
            App.toast('Attributes must be a JSON object; nothing was saved', 'error');
            return;
        }

        const payload = { email, name, status, attribs };
        if (Array.isArray(this._lists)) payload.lists = this._lists;
        try {
            await API.put(`/api/subscribers/${id}`, payload);
            App.toast('Subscriber updated', 'success');
            this.render();
        } catch {
            // API.request already surfaced the server message.
        }
    },

    async remove(id) {
        if (await App.confirm('Delete Subscriber', 'Are you sure you want to delete this subscriber?')) {
            try {
                await API.del(`/api/subscribers/${id}`);
                App.toast('Subscriber deleted', 'success');
                this.render();
            } catch {
                // API.request already surfaced the server message.
            }
        }
    },

    async exportAll() {
        try {
            const params = new URLSearchParams();
            if (this.query) params.set('search', this.query);
            if (this.listFilter) params.set('list_id', String(this.listFilter));
            const queryParam = params.toString() ? `?${params}` : '';
            const result = await API.get(`/api/subscribers/export-all${queryParam}`);
            if (result && result.blob) {
                API.downloadBlob(result.blob, 'subscribers_export.csv');
                App.toast('Export downloaded', 'success');
            }
        } catch {
            // API.request already surfaced the server message.
        }
    },
};
