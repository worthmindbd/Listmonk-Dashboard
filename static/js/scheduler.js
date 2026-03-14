/**
 * Campaign Scheduler page - Configure send windows for auto-pause/resume.
 */
const Scheduler = {
    schedule: null,
    _refreshInterval: null,

    async render() {
        App.setContent('<div class="loading-spinner">Loading scheduler...</div>');

        try {
            this.schedule = await API.get('/api/scheduler');
        } catch {
            App.setContent('<div class="empty-state"><h3>Failed to load scheduler</h3></div>');
            return;
        }

        this.renderPage();

        // Auto-refresh status every 30 seconds
        if (this._refreshInterval) clearInterval(this._refreshInterval);
        this._refreshInterval = setInterval(() => this.refreshStatus(), 30000);
    },

    renderPage() {
        const s = this.schedule;
        const enabled = s.enabled;
        const inWindow = s.in_send_window;

        // Status indicator
        let statusHtml;
        if (!enabled) {
            statusHtml = '<span class="badge badge-default" style="font-size:0.9rem;padding:6px 14px">DISABLED</span>';
        } else if (inWindow) {
            statusHtml = '<span class="badge badge-success" style="font-size:0.9rem;padding:6px 14px">SENDING - Inside send window</span>';
        } else {
            statusHtml = '<span class="badge badge-warning" style="font-size:0.9rem;padding:6px 14px">PAUSED - Outside send window</span>';
        }

        // Days checkboxes
        const allDays = [
            { key: 'mon', label: 'Mon' }, { key: 'tue', label: 'Tue' },
            { key: 'wed', label: 'Wed' }, { key: 'thu', label: 'Thu' },
            { key: 'fri', label: 'Fri' }, { key: 'sat', label: 'Sat' },
            { key: 'sun', label: 'Sun' },
        ];
        const activeDays = new Set(s.days || []);
        const daysHtml = allDays.map(d =>
            `<label class="checkbox-label"><input type="checkbox" name="schedDays" value="${d.key}" ${activeDays.has(d.key) ? 'checked' : ''}><span>${d.label}</span></label>`
        ).join('');

        // Time options
        const hourOptions = (selectedHour) => {
            let html = '';
            for (let h = 0; h < 24; h++) {
                const label = h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h - 12} PM`;
                html += `<option value="${h}" ${h === selectedHour ? 'selected' : ''}>${label}</option>`;
            }
            return html;
        };

        const minuteOptions = (selectedMin) => {
            let html = '';
            for (let m = 0; m < 60; m += 15) {
                html += `<option value="${m}" ${m === selectedMin ? 'selected' : ''}>${m.toString().padStart(2, '0')}</option>`;
            }
            return html;
        };

        // Timezone options
        const timezones = [
            'US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific',
            'UTC', 'Europe/London', 'Europe/Berlin', 'Europe/Paris',
            'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata',
            'Australia/Sydney', 'Pacific/Auckland',
        ];
        const tzOptions = timezones.map(tz =>
            `<option value="${tz}" ${tz === s.timezone ? 'selected' : ''}>${tz}</option>`
        ).join('');

        // Auto-paused campaigns
        const autoPaused = s.auto_paused_campaigns || [];

        const html = `
            <!-- Live Status -->
            <div class="card" style="margin-bottom:20px">
                <div class="card-header">
                    <h3 class="card-title">Campaign Send Window</h3>
                    <div class="action-btns">
                        ${statusHtml}
                        <button class="btn btn-sm" onclick="Scheduler.runNow()">Check Now</button>
                    </div>
                </div>
                <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
                    <div>
                        <span style="color:var(--text-muted);font-size:0.85rem">Current time:</span>
                        <strong id="schedulerCurrentTime">${s.current_time || '-'}</strong>
                    </div>
                    <div>
                        <span style="color:var(--text-muted);font-size:0.85rem">Window:</span>
                        <strong>${this.formatTime(s.start_hour, s.start_minute)} - ${this.formatTime(s.end_hour, s.end_minute)} ${s.timezone}</strong>
                    </div>
                    ${autoPaused.length ? `
                    <div>
                        <span style="color:var(--text-muted);font-size:0.85rem">Auto-paused campaigns:</span>
                        <strong style="color:var(--warning)">${autoPaused.length}</strong>
                    </div>` : ''}
                </div>
            </div>

            <!-- Configuration -->
            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">Schedule Configuration</h3>
                </div>

                <!-- Enable/Disable Toggle -->
                <div class="form-group" style="margin-bottom:20px">
                    <label class="checkbox-label" style="font-size:1rem;padding:12px 16px;border-width:2px;${enabled ? 'border-color:var(--success);background:var(--success-bg)' : ''}">
                        <input type="checkbox" id="schedEnabled" ${enabled ? 'checked' : ''} onchange="Scheduler.toggleEnabled(this.checked)">
                        <span style="font-weight:600">${enabled ? 'Scheduler is ON' : 'Scheduler is OFF'}</span>
                    </label>
                    <p style="font-size:0.85rem;color:var(--text-muted);margin-top:8px">
                        When enabled, running campaigns will auto-pause outside the send window and auto-resume when the window opens.
                        Manually paused campaigns are never affected.
                    </p>
                </div>

                <!-- Timezone -->
                <div class="form-group">
                    <label>Timezone</label>
                    <select id="schedTimezone" style="width:auto;min-width:200px">
                        ${tzOptions}
                    </select>
                </div>

                <!-- Send Window -->
                <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end;margin-bottom:16px">
                    <div class="form-group" style="margin-bottom:0">
                        <label>Start Time</label>
                        <div style="display:flex;gap:4px">
                            <select id="schedStartHour" style="width:auto">${hourOptions(s.start_hour)}</select>
                            <select id="schedStartMin" style="width:auto">${minuteOptions(s.start_minute)}</select>
                        </div>
                    </div>
                    <span style="color:var(--text-muted);font-size:1.2rem;padding-bottom:8px">to</span>
                    <div class="form-group" style="margin-bottom:0">
                        <label>End Time</label>
                        <div style="display:flex;gap:4px">
                            <select id="schedEndHour" style="width:auto">${hourOptions(s.end_hour)}</select>
                            <select id="schedEndMin" style="width:auto">${minuteOptions(s.end_minute)}</select>
                        </div>
                    </div>
                </div>

                <!-- Days -->
                <div class="form-group">
                    <label>Send on these days</label>
                    <div class="checkbox-group">${daysHtml}</div>
                </div>

                <!-- Visual Timeline -->
                <div style="margin-top:20px;margin-bottom:20px">
                    <label style="font-size:0.85rem;font-weight:500;color:var(--text-secondary);margin-bottom:8px;display:block">Daily Timeline</label>
                    <div style="position:relative;height:40px;background:var(--bg-input);border-radius:var(--radius);overflow:hidden;border:1px solid var(--border-color)">
                        <div style="position:absolute;left:${(s.start_hour * 60 + s.start_minute) / 1440 * 100}%;right:${100 - (s.end_hour * 60 + s.end_minute) / 1440 * 100}%;top:0;bottom:0;background:var(--success-bg);border-left:2px solid var(--success);border-right:2px solid var(--success)"></div>
                        ${this.renderTimeMarkers()}
                    </div>
                    <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--text-muted);margin-top:4px">
                        <span>12AM</span><span>3AM</span><span>6AM</span><span>9AM</span><span>12PM</span><span>3PM</span><span>6PM</span><span>9PM</span><span>12AM</span>
                    </div>
                </div>

                <!-- Save -->
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="Scheduler.save()">Save Schedule</button>
                </div>
            </div>

            <!-- How it works -->
            <div class="card" style="margin-top:20px">
                <div class="card-header"><h3 class="card-title">How it works</h3></div>
                <div style="font-size:0.85rem;color:var(--text-secondary);line-height:1.8">
                    <p><strong>Every 60 seconds</strong>, the scheduler checks:</p>
                    <ul style="padding-left:20px;margin:8px 0">
                        <li>If current time is <strong>outside</strong> the send window and a campaign is <strong>running</strong> -> it gets <strong>auto-paused</strong></li>
                        <li>If current time is <strong>inside</strong> the send window and a campaign was <strong>auto-paused</strong> -> it gets <strong>auto-resumed</strong></li>
                        <li>Campaigns you <strong>manually pause</strong> are never touched by the scheduler</li>
                    </ul>
                    <p>The scheduler only affects campaigns with status "running". Draft, finished, or cancelled campaigns are ignored.</p>
                </div>
            </div>
        `;

        App.setContent(html);
    },

    formatTime(hour, minute) {
        const ampm = hour >= 12 ? 'PM' : 'AM';
        const h = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
        return `${h}:${minute.toString().padStart(2, '0')} ${ampm}`;
    },

    renderTimeMarkers() {
        let html = '';
        for (let h = 0; h <= 24; h += 3) {
            const pct = (h / 24) * 100;
            html += `<div style="position:absolute;left:${pct}%;top:0;bottom:0;width:1px;background:var(--border-color)"></div>`;
        }
        return html;
    },

    async toggleEnabled(checked) {
        try {
            await API.put('/api/scheduler', { enabled: checked });
            App.toast(checked ? 'Scheduler enabled' : 'Scheduler disabled', 'success');
            this.schedule = await API.get('/api/scheduler');
            this.renderPage();
        } catch {
            App.toast('Failed to update scheduler', 'error');
        }
    },

    async save() {
        const data = {
            enabled: document.getElementById('schedEnabled').checked,
            timezone: document.getElementById('schedTimezone').value,
            start_hour: parseInt(document.getElementById('schedStartHour').value),
            start_minute: parseInt(document.getElementById('schedStartMin').value),
            end_hour: parseInt(document.getElementById('schedEndHour').value),
            end_minute: parseInt(document.getElementById('schedEndMin').value),
            days: Array.from(document.querySelectorAll('input[name="schedDays"]:checked')).map(cb => cb.value),
        };

        if (data.days.length === 0) {
            App.toast('Select at least one day', 'error');
            return;
        }

        try {
            await API.put('/api/scheduler', data);
            App.toast('Schedule saved', 'success');
            this.schedule = await API.get('/api/scheduler');
            this.renderPage();
        } catch {
            App.toast('Failed to save schedule', 'error');
        }
    },

    async runNow() {
        try {
            const result = await API.post('/api/scheduler/run');
            if (result.error) {
                App.toast(result.error, 'error');
            } else {
                const status = result.in_send_window ? 'Inside send window' : 'Outside send window';
                const paused = result.auto_paused_campaigns?.length || 0;
                App.toast(`${status} | ${paused} campaign(s) auto-paused`, 'info');
                this.schedule = await API.get('/api/scheduler');
                this.renderPage();
            }
        } catch {
            App.toast('Failed to run scheduler', 'error');
        }
    },

    async refreshStatus() {
        // Only refresh if we're still on the scheduler page
        if (App.currentPage !== 'scheduler') {
            if (this._refreshInterval) {
                clearInterval(this._refreshInterval);
                this._refreshInterval = null;
            }
            return;
        }
        try {
            this.schedule = await API.get('/api/scheduler');
            const el = document.getElementById('schedulerCurrentTime');
            if (el) el.textContent = this.schedule.current_time || '-';
        } catch {}
    },
};
