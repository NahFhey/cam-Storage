// App shell: top bar, navigation and theme. Load in <head> after ui.js and api.js.

(function (global) {
    'use strict';

    var THEME_KEY = 'cam_theme';

    function storedTheme() {
        try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
    }

    function applyTheme(theme) {
        if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
        else document.documentElement.removeAttribute('data-theme');
    }

    function currentTheme() {
        var attr = document.documentElement.getAttribute('data-theme');
        if (attr) return attr;
        return global.matchMedia && global.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    // Apply before first paint to avoid a theme flash
    applyTheme(storedTheme());

    var NAV = [
        { page: 'entry', href: '/static/index.html', label: 'Entry', icon: 'scan' },
        { page: 'hot-list', href: '/static/hot-list.html', label: 'Hot List', icon: 'flame' },
        { page: 'search', href: '/static/search.html', label: 'Search', icon: 'search' },
        { page: 'analytics', href: '/static/analytics.html', label: 'Analytics', icon: 'chart' },
        { page: 'admin', href: '/static/admin.html', label: 'Admin', icon: 'settings', admin: true }
    ];

    function render() {
        var U = global.UI;
        var page = document.body.dataset.page;
        var header = document.createElement('header');
        header.className = 'topbar';
        header.innerHTML =
            '<a class="brand" href="/static/index.html"><span class="brand-mark">' + U.icon('tool') + '</span>' +
            '<span class="brand-text">CAM Tracking</span></a>' +
            '<nav class="nav" aria-label="Main">' + NAV.map(function (item) {
                return '<a href="' + item.href + '"' + (item.admin ? ' data-admin-only' : '') +
                    (item.page === page ? ' aria-current="page"' : '') + '>' + U.icon(item.icon) + item.label + '</a>';
            }).join('') + '</nav>' +
            '<div class="topbar-end">' +
            '<button type="button" class="topbar-btn" id="themeToggle" aria-label="Toggle dark mode"></button>' +
            '<div id="userArea" class="row" style="gap:0.5rem"></div>' +
            '</div>';
        document.body.insertBefore(header, document.body.firstChild);

        var toggle = header.querySelector('#themeToggle');
        function paintToggle() {
            toggle.innerHTML = U.icon(currentTheme() === 'dark' ? 'sun' : 'moon');
            toggle.title = currentTheme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
        }
        toggle.addEventListener('click', function () {
            var next = currentTheme() === 'dark' ? 'light' : 'dark';
            applyTheme(next);
            try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* private mode */ }
            paintToggle();
        });
        paintToggle();
        refreshAdminLinks();
    }

    function refreshAdminLinks() {
        var admin = global.Session && global.Session.isAdmin();
        document.querySelectorAll('[data-admin-only]').forEach(function (el) {
            el.classList.toggle('hidden', !admin);
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
    else render();

    global.Layout = { refreshAdminLinks: refreshAdminLinks };
})(window);
