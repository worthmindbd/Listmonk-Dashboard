/**
 * Dashboard home page with summary stats and analytics charts.
 */
const Dashboard = {
    charts: {},
    campaignsData: [],

    getThemeColors() {
        const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
        return {
            isDark,
            textColor: isDark ? '#94a3b8' : '#64748d',
            gridColor: isDark ? 'rgba(255, 255, 255, 0.07)' : 'rgba(0, 55, 112, 0.06)',
            tooltipBg: isDark ? 'rgba(15, 23, 42, 0.92)' : 'rgba(255, 255, 255, 0.95)',
            tooltipText: isDark ? '#f8fafc' : '#0d253d',
            tooltipBorder: isDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(227, 232, 238, 0.9)',
        };
    },

    async render() {
        App.setContent('<div class="loading-spinner">Loading dashboard...</div>');

        try {
            // Fetch all data in parallel
            const [listsRes, campaignsRes, subscribersRes] = await Promise.allSettled([
                API.get('/api/lists?per_page=1&minimal=true'),
                API.get('/api/campaigns?per_page=1'),
                API.get('/api/subscribers?per_page=1'),
            ]);

            const totalLists = listsRes.status === 'fulfilled' ? (listsRes.value?.data?.total || 0) : 0;
            const totalCampaigns = campaignsRes.status === 'fulfilled' ? (campaignsRes.value?.data?.total || 0) : 0;
            const totalSubscribers = subscribersRes.status === 'fulfilled' ? (subscribersRes.value?.data?.total || 0) : 0;

            // Get recent campaigns for chart data
            let campaigns = [];
            if (campaignsRes.status === 'fulfilled') {
                campaigns = campaignsRes.value?.data?.results || [];
            }
            this.campaignsData = campaigns;

            let html = `
                <div class="stats-grid">
                    <div class="stat-card accent">
                        <div class="stat-label">Total Subscribers</div>
                        <div class="stat-value">${App.formatNumber(totalSubscribers)}</div>
                    </div>
                    <div class="stat-card success">
                        <div class="stat-label">Total Lists</div>
                        <div class="stat-value">${App.formatNumber(totalLists)}</div>
                    </div>
                    <div class="stat-card warning">
                        <div class="stat-label">Total Campaigns</div>
                        <div class="stat-value">${App.formatNumber(totalCampaigns)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Active Campaigns</div>
                        <div class="stat-value">${App.formatNumber(campaigns.filter(c => c.status === 'running').length)}</div>
                    </div>
                </div>

                <div class="charts-grid">
                    <div class="card">
                        <div class="card-header">
                            <h3 class="card-title">Campaign Performance</h3>
                        </div>
                        <div class="chart-container">
                            <canvas id="campaignChart"></canvas>
                        </div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <h3 class="card-title">Campaign Status Distribution</h3>
                        </div>
                        <div class="chart-container">
                            <canvas id="statusChart"></canvas>
                        </div>
                    </div>
                </div>

                <div class="card" style="margin-top:24px">
                    <div class="card-header">
                        <h3 class="card-title">Recent Campaigns</h3>
                        <a href="#/campaigns" class="btn btn-sm">View All</a>
                    </div>
                    <div class="table-wrapper" style="border:none;box-shadow:none;background:transparent">
                        <table>
                            <thead><tr>
                                <th>Name</th><th>Status</th><th>Lists</th><th>Created</th>
                            </tr></thead>
                            <tbody>`;

            campaigns.slice(0, 5).forEach(c => {
                const listNames = (c.lists || []).map(l => App.escapeHtml(l.name)).join(', ') || '-';
                html += `<tr style="cursor:pointer" onclick="Campaigns.showDetail(${c.id})">
                    <td><strong>${App.escapeHtml(c.name)}</strong></td>
                    <td>${App.statusBadge(c.status)}</td>
                    <td>${listNames}</td>
                    <td>${App.formatDate(c.created_at)}</td>
                </tr>`;
            });

            if (!campaigns.length) {
                html += '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:24px">No campaigns yet</td></tr>';
            }

            html += '</tbody></table></div></div>';
            App.setContent(html);

            // Render charts
            this.renderCampaignChart(campaigns);
            this.renderStatusChart(campaigns);

        } catch (err) {
            App.setContent(`<div class="empty-state"><h3>Failed to load dashboard</h3><p>${App.escapeHtml(err.message)}</p></div>`);
        }
    },

    renderCampaignChart(campaigns) {
        const ctx = document.getElementById('campaignChart');
        if (!ctx) return;

        const theme = this.getThemeColors();
        const recent = (campaigns || this.campaignsData).slice(0, 8).reverse();
        const labels = recent.map(c => c.name?.substring(0, 20) || 'Untitled');
        const sent = recent.map(c => c.to_send || 0);
        const views = recent.map(c => c.views || 0);
        const clicks = recent.map(c => c.clicks || 0);

        if (this.charts.campaign) this.charts.campaign.destroy();
        this.charts.campaign = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    { label: 'Sent', data: sent, backgroundColor: 'rgba(83, 58, 253, 0.82)', borderRadius: 6 },
                    { label: 'Views', data: views, backgroundColor: 'rgba(16, 185, 129, 0.82)', borderRadius: 6 },
                    { label: 'Clicks', data: clicks, backgroundColor: 'rgba(245, 158, 11, 0.82)', borderRadius: 6 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: theme.textColor,
                            font: { family: 'Plus Jakarta Sans', weight: 500, size: 12 },
                            usePointStyle: true,
                            pointStyle: 'circle',
                            padding: 18,
                        },
                    },
                    tooltip: {
                        backgroundColor: theme.tooltipBg,
                        titleColor: theme.tooltipText,
                        bodyColor: theme.tooltipText,
                        borderColor: theme.tooltipBorder,
                        borderWidth: 1,
                        padding: 12,
                        cornerRadius: 10,
                        bodyFont: { family: 'Plus Jakarta Sans' },
                        titleFont: { family: 'Plus Jakarta Sans', weight: 600 },
                    },
                },
                scales: {
                    x: {
                        ticks: { color: theme.textColor, font: { family: 'Plus Jakarta Sans', size: 11 } },
                        grid: { color: theme.gridColor, drawBorder: false },
                    },
                    y: {
                        ticks: { color: theme.textColor, font: { family: 'Plus Jakarta Sans', size: 11 } },
                        grid: { color: theme.gridColor, drawBorder: false },
                        beginAtZero: true,
                    },
                },
            },
        });
    },

    renderStatusChart(campaigns) {
        const ctx = document.getElementById('statusChart');
        if (!ctx) return;

        const theme = this.getThemeColors();
        const statusCounts = {};
        (campaigns || this.campaignsData).forEach(c => {
            statusCounts[c.status] = (statusCounts[c.status] || 0) + 1;
        });

        const colorMap = {
            draft: '#3b82f6',
            running: '#10b981',
            finished: '#533afd',
            paused: '#f59e0b',
            cancelled: '#ea2261',
            scheduled: '#8b5cf6',
        };

        const labels = Object.keys(statusCounts);
        const data = Object.values(statusCounts);
        const bgColors = labels.map(s => colorMap[s] || '#64748b');

        if (this.charts.status) this.charts.status.destroy();
        this.charts.status = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{
                    data,
                    backgroundColor: bgColors,
                    borderWidth: 2,
                    borderColor: theme.isDark ? '#0f172a' : '#ffffff',
                    hoverOffset: 6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: theme.textColor,
                            padding: 16,
                            font: { family: 'Plus Jakarta Sans', weight: 500, size: 12 },
                            usePointStyle: true,
                            pointStyle: 'circle',
                        },
                    },
                    tooltip: {
                        backgroundColor: theme.tooltipBg,
                        titleColor: theme.tooltipText,
                        bodyColor: theme.tooltipText,
                        borderColor: theme.tooltipBorder,
                        borderWidth: 1,
                        padding: 12,
                        cornerRadius: 10,
                        bodyFont: { family: 'Plus Jakarta Sans' },
                    },
                },
            },
        });
    },
};

// Listen to theme switch to dynamically update charts
window.addEventListener('themeChanged', () => {
    if (App.currentPage === 'dashboard') {
        Dashboard.renderCampaignChart();
        Dashboard.renderStatusChart();
    }
});

