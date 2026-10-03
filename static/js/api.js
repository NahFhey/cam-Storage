// API client for the CAM Tracking Kiosk.

(function (global) {
    'use strict';

    var API_BASE = '/api';
    var TOKEN_KEY = 'cam_auth_token';
    var USER_KEY = 'cam_current_user';

    // ========== Session storage ==========

    function storageGet(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }
    function storageSet(key, value) {
        try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
    }
    function storageRemove(key) {
        try { localStorage.removeItem(key); } catch (e) { /* private mode */ }
    }

    var Session = {
        token: function () { return storageGet(TOKEN_KEY); },
        user: function () {
            try { return JSON.parse(storageGet(USER_KEY)); } catch (e) { return null; }
        },
        save: function (token, user) {
            if (token) storageSet(TOKEN_KEY, token);
            if (user) storageSet(USER_KEY, JSON.stringify(user));
        },
        clear: function () { storageRemove(TOKEN_KEY); storageRemove(USER_KEY); },
        isLoggedIn: function () { return !!storageGet(TOKEN_KEY); },
        isAdmin: function () { var u = Session.user(); return !!u && u.role === 'admin'; }
    };

    // ========== Request core ==========

    async function errorMessage(response) {
        try {
            var body = await response.clone().json();
            if (typeof body.detail === 'string') return body.detail;
            if (Array.isArray(body.detail) && body.detail.length) {
                // Pydantic validation errors
                return body.detail.map(function (d) { return d.msg.replace(/^Value error, /, ''); }).join('; ');
            }
        } catch (e) {
            try {
                var text = await response.text();
                if (text) return text;
            } catch (e2) { /* ignore */ }
        }
        return 'Request failed (' + response.status + ')';
    }

    /**
     * Call the API. Returns parsed JSON, or the raw Response for non-JSON bodies.
     * options: fetch options plus `json` (body to serialize) and `raw` (skip parsing).
     */
    async function request(endpoint, options) {
        options = options || {};
        var headers = Object.assign({}, options.headers);
        var token = Session.token();
        if (token) headers.Authorization = 'Bearer ' + token;
        var init = { method: options.method || 'GET', headers: headers };
        if (options.json !== undefined) {
            headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(options.json);
        } else if (options.body) {
            init.body = options.body;
        }

        var response;
        try {
            response = await fetch(API_BASE + endpoint, init);
        } catch (e) {
            throw new Error('Cannot reach the server. Check that it is running.');
        }

        if (response.status === 401 && token) {
            Session.clear();
            global.dispatchEvent(new CustomEvent('sessionExpired'));
            throw new Error('Your session has expired. Please log in again.');
        }
        if (response.status === 403) throw new Error('Admin access required for this action.');
        if (!response.ok) throw new Error(await errorMessage(response));

        var type = response.headers.get('content-type') || '';
        if (!options.raw && type.indexOf('application/json') >= 0) return response.json();
        return response;
    }

    function qs(params) {
        var clean = {};
        Object.keys(params || {}).forEach(function (k) {
            if (params[k] !== undefined && params[k] !== null && params[k] !== '') clean[k] = params[k];
        });
        var s = new URLSearchParams(clean).toString();
        return s ? '?' + s : '';
    }

    async function download(endpoint, filename) {
        var response = await request(endpoint, { raw: true });
        var blob = await response.blob();
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    // ========== Endpoints ==========

    var API = {
        request: request,
        download: download,

        // Auth
        login: async function (pin) {
            var data = await request('/auth/login', { method: 'POST', json: { pin: pin } });
            Session.save(data.token, data.user);
            return data;
        },
        logout: async function () {
            try { await request('/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
            Session.clear();
        },
        me: function () { return request('/auth/me'); },

        // Users
        users: function () { return request('/users'); },
        createUser: function (data) { return request('/users', { method: 'POST', json: data }); },
        updateUser: function (id, data) { return request('/users/' + id, { method: 'PATCH', json: data }); },
        deactivateUser: function (id) { return request('/users/' + id, { method: 'DELETE' }); },

        // Jobs
        jobs: async function () {
            var data = await request('/jobs?limit=500');
            return data.items;
        },
        job: function (id) { return request('/jobs/' + id); },
        jobToolStats: function (id) { return request('/jobs/' + id + '/tool-stats'); },
        createJob: function (data) { return request('/jobs', { method: 'POST', json: data }); },
        updateJob: function (id, data) { return request('/jobs/' + id, { method: 'PATCH', json: data }); },
        deleteJob: function (id) { return request('/jobs/' + id, { method: 'DELETE' }); },

        // Tools
        camItem: function (id) { return request('/cam-items/' + id); },
        updateCamItem: function (id, data) { return request('/cam-items/' + id, { method: 'PATCH', json: data }); },
        createCamItemsBulk: function (data) { return request('/cam-items/bulk', { method: 'POST', json: data }); },
        camLifespan: function (id) { return request('/cam-items/' + id + '/lifespan'); },

        // Moves
        resolveEntry: function (entry) { return request('/resolve-entry', { method: 'POST', json: { entry: entry } }); },
        moveCam: function (data) { return request('/moves', { method: 'POST', json: data }); },
        moveSet: function (data) { return request('/moves/set', { method: 'POST', json: data }); },
        undoMove: function (camItemId) { return request('/moves/undo/' + camItemId, { method: 'POST' }); },
        moves: function (params) { return request('/moves' + qs(params)); },

        // Priority
        hotList: function () { return request('/hot-list'); },
        top5: function () { return request('/top5'); },
        reorderTop5: function (jobIds) { return request('/top5/reorder', { method: 'POST', json: { job_ids: jobIds } }); },
        priorityChanges: function (params) { return request('/priority-changes' + qs(params)); },
        priorityAnalytics: function (days) { return request('/priority-changes/analytics' + qs({ days: days })); },

        // Search & analytics
        search: function (q) { return request('/search' + qs({ q: q })); },
        analytics: function (name, params) { return request('/analytics/' + name + qs(params)); },

        // Config
        config: function () { return request('/config'); },
        updateConfig: function (data) { return request('/config', { method: 'PATCH', json: data }); },

        // Data
        exportCsv: function (name) { return download('/export/' + name + '/csv', name.replace(/-/g, '_') + '.csv'); },
        exportDatabase: function () { return download('/export/database', 'cam_tracking_backup.db'); },
        importDatabase: function (file) {
            var form = new FormData();
            form.append('file', file);
            return request('/import/database', { method: 'POST', body: form });
        }
    };

    global.API = API;
    global.Session = Session;
})(window);
