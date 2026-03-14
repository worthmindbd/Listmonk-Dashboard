/**
 * API client wrapper for the ListMonk Dashboard backend.
 */
const API = {
    async request(method, path, body = null, isFormData = false) {
        const opts = {
            method,
            headers: {},
        };

        if (body && !isFormData) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        } else if (body && isFormData) {
            opts.body = body; // FormData sets its own Content-Type
        }

        try {
            const resp = await fetch(path, opts);
            if (!resp.ok) {
                if (resp.status === 401) {
                    window.location.href = '/auth/login';
                    return;
                }
                let detail = `HTTP ${resp.status}`;
                try {
                    const err = await resp.json();
                    detail = err.detail || err.message || detail;
                } catch {}
                throw new Error(detail);
            }

            const contentType = resp.headers.get('content-type') || '';
            if (contentType.includes('text/csv')) {
                return { blob: await resp.blob(), stats: resp.headers.get('x-conversion-stats') };
            }
            if (contentType.includes('text/html')) {
                return { html: await resp.text() };
            }
            return await resp.json();
        } catch (err) {
            App.toast(err.message, 'error');
            throw err;
        }
    },

    get(path) { return this.request('GET', path); },
    post(path, body) { return this.request('POST', path, body); },
    put(path, body) { return this.request('PUT', path, body); },
    del(path) { return this.request('DELETE', path); },
    upload(path, formData) { return this.request('POST', path, formData, true); },

    // Download helper
    downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }
};
