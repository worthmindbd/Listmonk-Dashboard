/**
 * Bounces page - list view with filters.
 */
const Bounces = {
    page: 1,
    campaign_id: '',
    bounce_type: '',
    campaigns: [],

    async render() {
        try {
            // Load campaigns for filter
            const campRes = await API.get('/api/campaigns?per_page=500&minimal=true');
            this.campaigns = campRes.data?.results || [];

            // Load bounces
            let params = `page=${this.page}&per_page=50`;
            if (this.campaign_id) params += `&campaign_id=${this.campaign_id}`;
            if (this.bounce_type) params += `&bounce_type=${this.bounce_type}`;

            const result = await API.get(`/api/bounces?${params}`);
            const data = result?.data || {};
            const bounces = data.results || [];
            const total = data.total || 0;

            App.setActions(`
                <button class="btn btn-sm" onclick="Bounces.exportCSV()">Export CSV</button>
            `);

            // Build campaign options
            const campOptions = this.campaigns.map(c =>
                `<option value="${c.id}" ${this.campaign_id == c.id ? 'selected' : ''}>${App.escapeHtml(c.name)}</option>`
            ).join('');

            let html = `
                <div class="search-bar">
                    <div class="form-group" style="min-width:200px;margin:0">
                        <select id="bounceCamp" onchange="Bounces.filterCamp(this.value)" style="width:100%">
                            <option value="">All Campaigns</option>
                            ${campOptions}
                        </select>
                    </div>
                    <div class="form-group" style="min-width:140px;margin:0">
                        <select id="bounceType" onchange="Bounces.filterType(this.value)" style="width:100%">
                            <option value="" ${!this.bounce_type ? 'selected' : ''}>All Types</option>
                            <option value="hard" ${this.bounce_type === 'hard' ? 'selected' : ''}>Hard</option>
                            <option value="soft" ${this.bounce_type === 'soft' ? 'selected' : ''}>Soft</option>
                        </select>
                    </div>
                    ${this.campaign_id || this.bounce_type ? '<button class="btn btn-sm btn-secondary" onclick="Bounces.clearFilters()">Clear</button>' : ''}
                </div>
                <div class="table-wrapper"><table>
                    <thead><tr>
                        <th>Email</th><th>Campaign</th><th>Type</th><th>Source</th><th>Date</th><th>Actions</th>
                    </tr></thead><tbody>`;

            if (!bounces.length) {
                html += '<tr><td colspan="6"><div class="empty-state"><h3>No bounces found</h3></div></td></tr>';
            }

            bounces.forEach(b => {
                const typeBadge = b.type === 'hard'
                    ? '<span class="badge badge-danger">Hard</span>'
                    : b.type === 'soft'
                        ? '<span class="badge badge-warning">Soft</span>'
                        : `<span class="badge badge-default">${App.escapeHtml(b.type || '-')}</span>`;

                html += `<tr>
                    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${App.escapeHtml(b.email || '')}">${App.escapeHtml(b.email || '-')}</td>
                    <td>${App.escapeHtml(b.campaign?.name || '-')}</td>
                    <td>${typeBadge}</td>
                    <td>${App.escapeHtml(b.source || '-')}</td>
                    <td>${App.formatDate(b.created_at)}</td>
                    <td class="action-btns">
                        <button class="btn btn-sm btn-danger" onclick="Bounces.remove(${b.id})">Delete</button>
                    </td>
                </tr>`;
            });

            html += '</tbody></table></div>';
            html += App.renderPagination(this.page, total, 50, 'Bounces.goToPage');
            App.setContent(html);
        } catch {
            App.setContent('<div class="empty-state"><h3>Failed to load bounces</h3></div>');
        }
    },

    filterCamp(val) {
        this.campaign_id = val;
        this.page = 1;
        this.render();
    },

    filterType(val) {
        this.bounce_type = val;
        this.page = 1;
        this.render();
    },

    clearFilters() {
        this.campaign_id = '';
        this.bounce_type = '';
        this.page = 1;
        this.render();
    },

    goToPage(p) { this.page = p; this.render(); },

    async remove(id) {
        if (await App.confirm('Delete Bounce', 'Delete this bounce record?')) {
            await API.del(`/api/bounces/${id}`);
            App.toast('Bounce deleted', 'success');
            this.render();
        }
    },

    async exportCSV() {
        let params = '';
        if (this.campaign_id) params = `?campaign_id=${this.campaign_id}`;
        if (this.bounce_type) params += params ? `&bounce_type=${this.bounce_type}` : `?bounce_type=${this.bounce_type}`;
        window.location.href = `/api/bounces/export${params}`;
    },
};