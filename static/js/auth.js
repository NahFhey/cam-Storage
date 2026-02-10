// Auth UI - login overlay, session management, nav updates
// Include this file on every page AFTER api.js

(function() {
    'use strict';

    // ========== Login Overlay ==========

    function createLoginOverlay() {
        const overlay = document.createElement('div');
        overlay.id = 'loginOverlay';
        overlay.innerHTML = `
            <div class="login-box">
                <h2>CAM Tracking Kiosk</h2>
                <p class="login-subtitle">Enter your PIN to log in</p>
                <div id="loginAlerts"></div>
                <input
                    type="password"
                    id="loginPinInput"
                    class="login-pin-input"
                    placeholder="Enter PIN"
                    maxlength="20"
                    autocomplete="off"
                    inputmode="numeric"
                >
                <button id="loginBtn" class="login-btn">Log In</button>
            </div>
        `;
        document.body.appendChild(overlay);

        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            #loginOverlay {
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background: rgba(30, 41, 59, 0.95);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 9999;
            }
            #loginOverlay.hidden { display: none; }
            .login-box {
                background: white;
                border-radius: 1rem;
                padding: 3rem;
                width: 90%;
                max-width: 400px;
                text-align: center;
                box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            }
            .login-box h2 {
                font-size: 1.75rem;
                font-weight: 700;
                margin-bottom: 0.5rem;
                color: #1e293b;
            }
            .login-subtitle {
                color: #64748b;
                margin-bottom: 1.5rem;
                font-size: 1.1rem;
            }
            .login-pin-input {
                width: 100%;
                padding: 1.25rem;
                font-size: 2rem;
                text-align: center;
                border: 3px solid #cbd5e1;
                border-radius: 0.75rem;
                margin-bottom: 1.5rem;
                font-weight: 700;
                letter-spacing: 0.5rem;
                font-family: inherit;
                transition: border-color 0.2s;
            }
            .login-pin-input:focus {
                outline: none;
                border-color: #2563eb;
            }
            .login-btn {
                width: 100%;
                padding: 1.25rem;
                font-size: 1.25rem;
                font-weight: 700;
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 0.75rem;
                cursor: pointer;
                transition: background 0.2s;
                min-height: 60px;
            }
            .login-btn:hover { background: #1d4ed8; }
            .login-btn:disabled { opacity: 0.5; cursor: not-allowed; }
            #loginAlerts .alert {
                padding: 0.75rem 1rem;
                border-radius: 0.5rem;
                margin-bottom: 1rem;
                font-weight: 500;
            }
            #loginAlerts .alert-error {
                background: #fee2e2;
                color: #991b1b;
                border: 1px solid #fca5a5;
            }
            /* Nav user info */
            .nav-user-info {
                display: flex;
                align-items: center;
                gap: 0.75rem;
                margin-left: 1rem;
                padding-left: 1rem;
                border-left: 1px solid rgba(255,255,255,0.3);
            }
            .nav-user-name {
                color: #93c5fd;
                font-weight: 600;
                font-size: 0.95rem;
            }
            .nav-user-role {
                background: rgba(255,255,255,0.15);
                color: white;
                padding: 0.2rem 0.5rem;
                border-radius: 0.25rem;
                font-size: 0.75rem;
                text-transform: uppercase;
                font-weight: 600;
            }
            .nav-logout-btn {
                background: rgba(255,255,255,0.1);
                color: white;
                border: 1px solid rgba(255,255,255,0.3);
                padding: 0.4rem 0.75rem;
                border-radius: 0.5rem;
                cursor: pointer;
                font-size: 0.875rem;
                font-weight: 500;
                transition: background 0.2s;
            }
            .nav-logout-btn:hover { background: rgba(255,255,255,0.2); }
        `;
        document.head.appendChild(style);

        // Event listeners
        const pinInput = document.getElementById('loginPinInput');
        const loginBtn = document.getElementById('loginBtn');

        loginBtn.addEventListener('click', handleLogin);
        pinInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') handleLogin();
        });
    }

    async function handleLogin() {
        const pinInput = document.getElementById('loginPinInput');
        const loginBtn = document.getElementById('loginBtn');
        const alertsDiv = document.getElementById('loginAlerts');

        const pin = pinInput.value.trim();
        if (!pin) {
            alertsDiv.innerHTML = '<div class="alert alert-error">Please enter your PIN</div>';
            return;
        }

        loginBtn.disabled = true;
        loginBtn.textContent = 'Logging in...';
        alertsDiv.innerHTML = '';

        try {
            const result = await authLogin(pin);
            hideLoginOverlay();
            updateNavUser();
            updateAdminVisibility();
            // Dispatch event so pages can react
            window.dispatchEvent(new CustomEvent('userLoggedIn', { detail: result.user }));
        } catch (error) {
            alertsDiv.innerHTML = `<div class="alert alert-error">${error.message}</div>`;
            pinInput.value = '';
            pinInput.focus();
        } finally {
            loginBtn.disabled = false;
            loginBtn.textContent = 'Log In';
        }
    }

    // Expose globally so apiCall can trigger it
    window.showLoginOverlay = function() {
        const overlay = document.getElementById('loginOverlay');
        if (overlay) {
            overlay.classList.remove('hidden');
            const input = document.getElementById('loginPinInput');
            if (input) {
                input.value = '';
                input.focus();
            }
        }
    };

    function hideLoginOverlay() {
        const overlay = document.getElementById('loginOverlay');
        if (overlay) overlay.classList.add('hidden');
    }

    // ========== Nav Bar Updates ==========

    function updateNavUser() {
        // Remove existing user info
        const existing = document.querySelector('.nav-user-info');
        if (existing) existing.remove();

        const user = getCurrentUser();
        if (!user) return;

        const nav = document.querySelector('nav');
        if (!nav) return;

        const userInfo = document.createElement('div');
        userInfo.className = 'nav-user-info';
        userInfo.innerHTML = `
            <span class="nav-user-name">${user.display_name}</span>
            <span class="nav-user-role">${user.role}</span>
            <button class="nav-logout-btn" id="navLogoutBtn">Logout</button>
        `;
        nav.appendChild(userInfo);

        document.getElementById('navLogoutBtn').addEventListener('click', async () => {
            await authLogout();
            window.showLoginOverlay();
            updateNavUser();
            updateAdminVisibility();
        });
    }

    function updateAdminVisibility() {
        // Hide/show admin nav link based on role
        const adminLink = document.querySelector('nav .nav-links a[href*="admin.html"]');
        if (adminLink) {
            adminLink.style.display = isAdmin() ? '' : 'none';
        }
    }

    // ========== Session Check on Page Load ==========

    async function checkSession() {
        createLoginOverlay();

        if (!isLoggedIn()) {
            window.showLoginOverlay();
            updateNavUser();
            updateAdminVisibility();
            return;
        }

        // Validate the token is still good
        try {
            const me = await authGetMe();
            setCurrentUser(me);
            hideLoginOverlay();
            updateNavUser();
            updateAdminVisibility();
        } catch (e) {
            clearAuthToken();
            window.showLoginOverlay();
            updateNavUser();
            updateAdminVisibility();
        }
    }

    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', checkSession);
    } else {
        checkSession();
    }

})();
