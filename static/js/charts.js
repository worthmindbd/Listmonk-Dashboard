/**
 * Dashboard home page with summary stats and analytics charts.
 */
const Dashboard = {
    charts: {},

    async render() {
        App.setContent('<div class="loading-spinner">Loading dashboard...</div>');

        try {
            // Fetch all data in parallel
            const [listsRes, campaignsRes, subscribersRes, unblockRes] = await Promise.allSettled([
                API.get('/api/lists?per_page=1&minimal=true'),
                API.get('/api/campaigns?per_page=1'),
                API.get('/api/subscribers?per_page=1'),
                API.get('/api/auto-unblock/status'),
            ]);

            const totalLists = listsRes.status === 'fulfilled' ? (listsRes.value?.data?.total || 0) : 0;
            const totalCampaigns = campaignsRes.status === 'fulfilled' ? (campaignsRes.value?.data?.total || 0) : 0;
            const totalSubscribers = subscribersRes.status === 'fulfilled' ? (subscribersRes.value?.data?.total || 0) : 0;
            const blocklistedClickers = unblockRes.status === 'fulfilled' ? (unblockRes.value?.blocklisted_clickers || 0) : 0;

            // Get recent campaigns for chart data
            let campaigns = [];
            if (campaignsRes.status === 'fulfilled') {
                campaigns = campaignsRes.value?.data?.results || [];
            }

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

                <!-- Auto-Unblock Status -->
                <div class="card" style="margin-bottom:20px;${blocklistedClickers > 0 ? 'border-color:var(--warning)' : ''}">
                    <div class="card-header">
                        <h3 class="card-title">Auto-Unblock Protection</h3>
                        <div class="action-btns">
                            <span class="badge badge-${blocklistedClickers > 0 ? 'warning' : 'success'}">${blocklistedClickers > 0 ? blocklistedClickers + ' need unblocking' : 'All clear'}</span>
                            <button class="btn btn-sm btn-primary" onclick="Dashboard.runUnblock()">Run Now</button>
                        </div>
                    </div>
                    <p style="font-size:0.85rem;color:var(--text-secondary)">
                        Automatically detects subscribers who clicked links in campaigns but got blocklisted (from bounces).
                        Runs every 6 hours in the background. Clickers are real engaged users and should not be blocked.
                    </p>
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

                <div class="card" style="margin-top:20px">
                    <div class="card-header">
                        <h3 class="card-title">Recent Campaigns</h3>
                        <a href="#/campaigns" class="btn btn-sm">View All</a>
                    </div>
                    <div class="table-wrapper" style="border:none">
                        <table>
                            <thead><tr>
                                <th>Name</th><th>Status</th><th>Lists</th><th>Created</th>
                            </tr></thead>
                            <tbody>`;

            campaigns.slice(0, 5).forEach(c => {
                const listNames = (c.lists || []).map(l => l.name).join(', ') || '-';
                html += `<tr>
                    <td><strong>${c.name}</strong></td>
                    <td>${App.statusBadge(c.status)}</td>
                    <td>${listNames}</td>
                    <td>${App.formatDate(c.created_at)}</td>
                </tr>`;
            });

            if (!campaigns.length) {
                html += '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No campaigns yet</td></tr>';
            }

            html += '</tbody></table></div></div>';
            App.setContent(html);

            // Render charts
            this.renderCampaignChart(campaigns);
            this.renderStatusChart(campaigns);

        } catch (err) {
            App.setContent(`<div class="empty-state"><h3>Failed to load dashboard</h3><p>${err.message}</p></div>`);
        }
    },

    async runUnblock() {
        App.toast('Running auto-unblock...', 'info');
        try {
            const result = await API.post('/api/auto-unblock/run');
            if (result.error) {
                App.toast(result.error, 'error');
            } else if (result.success > 0) {
                App.toast(`Unblocked ${result.success} subscriber(s)`, 'success');
                this.render(); // Refresh dashboard
            } else {
                App.toast('No blocklisted clickers found', 'success');
            }
        } catch {
            App.toast('Failed to run auto-unblock', 'error');
        }
    },

    renderCampaignChart(campaigns) {
        const ctx = document.getElementById('campaignChart');
        if (!ctx) return;

        const recent = campaigns.slice(0, 8).reverse();
        const labels = recent.map(c => c.name?.substring(0, 20) || 'Untitled');
        const sent = recent.map(c => c.to_send || 0);
        const views = recent.map(c => {
            if (c.stats) return c.stats.views || 0;
            return 0;
        });
        const clicks = recent.map(c => {
            if (c.stats) return c.stats.clicks || 0;
            return 0;
        });

        if (this.charts.campaign) this.charts.campaign.destroy();
        this.charts.campaign = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    { label: 'Sent', data: sent, backgroundColor: 'rgba(99, 102, 241, 0.7)', borderRadius: 4 },
                    { label: 'Views', data: views, backgroundColor: 'rgba(34, 197, 94, 0.7)', borderRadius: 4 },
                    { label: 'Clicks', data: clicks, backgroundColor: 'rgba(245, 158, 11, 0.7)', borderRadius: 4 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#8b8fa3' } },
                },
                scales: {
                    x: { ticks: { color: '#8b8fa3' }, grid: { color: '#2a2e3f' } },
                    y: { ticks: { color: '#8b8fa3' }, grid: { color: '#2a2e3f' }, beginAtZero: true },
                },
            },
        });
    },

    renderStatusChart(campaigns) {
        const ctx = document.getElementById('statusChart');
        if (!ctx) return;

        const statusCounts = {};
        campaigns.forEach(c => {
            statusCounts[c.status] = (statusCounts[c.status] || 0) + 1;
        });

        const colorMap = {
            draft: '#3b82f6', running: '#22c55e', finished: '#6366f1',
            paused: '#f59e0b', cancelled: '#ef4444', scheduled: '#8b5cf6',
        };

        const labels = Object.keys(statusCounts);
        const data = Object.values(statusCounts);
        const bgColors = labels.map(s => colorMap[s] || '#5a5e72');

        if (this.charts.status) this.charts.status.destroy();
        this.charts.status = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels,
                datasets: [{ data, backgroundColor: bgColors, borderWidth: 0 }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { color: '#8b8fa3', padding: 16 } },
                },
            },
        });
    },
};
