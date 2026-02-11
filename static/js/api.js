// API utility functions for CAM Tracking Kiosk

const API_BASE = '/api';

// ========== Auth Token Management ==========

function getAuthToken() {
    return localStorage.getItem('cam_auth_token');
}

function setAuthToken(token) {
    localStorage.setItem('cam_auth_token', token);
}

function clearAuthToken() {
    localStorage.removeItem('cam_auth_token');
    localStorage.removeItem('cam_current_user');
}

function getCurrentUser() {
    const data = localStorage.getItem('cam_current_user');
    return data ? JSON.parse(data) : null;
}

function setCurrentUser(user) {
    localStorage.setItem('cam_current_user', JSON.stringify(user));
}

function isLoggedIn() {
    return !!getAuthToken();
}

function isAdmin() {
    const user = getCurrentUser();
    return user && user.role === 'admin';
}

// Generic fetch wrapper with error handling and auth
async function apiCall(endpoint, options = {}) {
    try {
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };

        // Auto-inject auth token
        const token = getAuthToken();
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers
        });

        // Handle auth errors
        if (response.status === 401) {
            clearAuthToken();
            if (typeof showLoginOverlay === 'function') {
                showLoginOverlay();
            }
            throw new Error('Session expired. Please log in again.');
        }

        if (response.status === 403) {
            throw new Error('Admin access required for this action.');
        }

        if (!response.ok) {
            let message = `API error: ${response.status}`;
            try {
                const error = await response.json();
                message = error.detail || message;
            } catch (e) {
                // Response wasn't JSON (e.g. plain text 500 error)
                try {
                    const text = await response.text();
                    if (text) message = text;
                } catch (e2) { /* ignore */ }
            }
            throw new Error(message);
        }

        // Handle non-JSON responses (like CSV downloads)
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            return await response.json();
        }

        return response;
    } catch (error) {
        console.error('API call failed:', error);
        throw error;
    }
}

// ========== Auth API ==========

async function authLogin(pin) {
    // Login doesn't need auth token, call fetch directly
    const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin })
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Login failed');
    }

    const data = await response.json();
    setAuthToken(data.token);
    setCurrentUser(data.user);
    return data;
}

async function authLogout() {
    try {
        await apiCall('/auth/logout', { method: 'POST' });
    } catch (e) {
        // Ignore errors on logout
    }
    clearAuthToken();
}

async function authGetMe() {
    return apiCall('/auth/me');
}

// ========== Users API (admin) ==========

async function getUsers() {
    return apiCall('/users');
}

async function createUser(userData) {
    return apiCall('/users', {
        method: 'POST',
        body: JSON.stringify(userData)
    });
}

async function updateUser(userId, updates) {
    return apiCall(`/users/${userId}`, {
        method: 'PATCH',
        body: JSON.stringify(updates)
    });
}

async function deleteUser(userId) {
    return apiCall(`/users/${userId}`, {
        method: 'DELETE'
    });
}

// Jobs
async function getJobs() {
    const response = await apiCall('/jobs');
    return response.items || response;
}

async function getJob(jobId) {
    return apiCall(`/jobs/${jobId}`);
}

async function createJob(jobData) {
    return apiCall('/jobs', {
        method: 'POST',
        body: JSON.stringify(jobData)
    });
}

async function updateJob(jobId, updates) {
    return apiCall(`/jobs/${jobId}`, {
        method: 'PATCH',
        body: JSON.stringify(updates)
    });
}

async function deleteJob(jobId) {
    return apiCall(`/jobs/${jobId}`, {
        method: 'DELETE'
    });
}

// CAM Items
async function getCamItems(filters = {}) {
    const params = new URLSearchParams(filters);
    return apiCall(`/cam-items?${params}`);
}

async function getCamItem(camItemId) {
    return apiCall(`/cam-items/${camItemId}`);
}

async function createCamItem(itemData) {
    return apiCall('/cam-items', {
        method: 'POST',
        body: JSON.stringify(itemData)
    });
}

async function updateCamItem(camItemId, updates) {
    return apiCall(`/cam-items/${camItemId}`, {
        method: 'PATCH',
        body: JSON.stringify(updates)
    });
}

async function createCamItemsBulk(bulkData) {
    return apiCall('/cam-items/bulk', {
        method: 'POST',
        body: JSON.stringify(bulkData)
    });
}

// Entry Resolution
async function resolveEntry(entry) {
    return apiCall('/resolve-entry', {
        method: 'POST',
        body: JSON.stringify({ entry })
    });
}

// Moves
async function moveCam(moveData) {
    return apiCall('/moves', {
        method: 'POST',
        body: JSON.stringify(moveData)
    });
}

async function undoMove(camItemId) {
    return apiCall(`/moves/undo/${camItemId}`, {
        method: 'POST'
    });
}

async function getMoves(filters = {}) {
    const params = new URLSearchParams(filters);
    return apiCall(`/moves?${params}`);
}

// Hot List
async function getHotList() {
    return apiCall('/hot-list');
}

// Search
async function search(query) {
    const params = new URLSearchParams({ q: query });
    return apiCall(`/search?${params}`);
}

// Analytics
async function getStationCounts() {
    return apiCall('/analytics/station-counts');
}

async function getRecentMoves(days = 7) {
    return apiCall(`/analytics/moves-recent?days=${days}`);
}

async function getDwellTimes() {
    return apiCall('/analytics/dwell-times');
}

async function getCycleCounts(limit = 20) {
    return apiCall(`/analytics/cycle-counts?limit=${limit}`);
}

async function getSharpenBacklog() {
    return apiCall('/analytics/sharpen-backlog');
}

// Configuration
async function getConfig() {
    return apiCall('/config');
}

async function updateConfig(configData) {
    return apiCall('/config', {
        method: 'PATCH',
        body: JSON.stringify(configData)
    });
}

// Exports
function exportJobsCSV() {
    window.location.href = `${API_BASE}/export/jobs/csv`;
}

function exportCamItemsCSV() {
    window.location.href = `${API_BASE}/export/cam-items/csv`;
}

function exportMovesCSV() {
    window.location.href = `${API_BASE}/export/moves/csv`;
}

function exportDatabase() {
    window.location.href = `${API_BASE}/export/database`;
}

async function importDatabase() {
    const fileInput = document.getElementById('dbFileInput');
    const file = fileInput.files[0];

    if (!file) {
        showAlertInContainer('importAlerts', 'Please select a database file to import', 'error');
        return;
    }

    if (!file.name.match(/\.(db|sqlite|sqlite3)$/i)) {
        showAlertInContainer('importAlerts', 'Please select a valid SQLite database file (.db, .sqlite, or .sqlite3)', 'error');
        return;
    }

    if (!confirm('⚠️ WARNING: This will PERMANENTLY REPLACE all current data!\n\nAre you absolutely sure you want to continue?')) {
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const headers = {};
        const token = getAuthToken();
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(`${API_BASE}/import/database`, {
            method: 'POST',
            headers,
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Import failed');
        }

        const result = await response.json();
        showAlertInContainer('importAlerts', `Database imported successfully! Jobs: ${result.jobs_count}, CAMs: ${result.cam_items_count}, Moves: ${result.moves_count}`, 'success');

        // Clear file input
        fileInput.value = '';

        // Reload the page after a short delay to refresh all data
        setTimeout(() => {
            window.location.reload();
        }, 2000);
    } catch (error) {
        showAlertInContainer('importAlerts', `Import failed: ${error.message}`, 'error');
    }
}

function showAlertInContainer(containerId, message, type) {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`Alert container '${containerId}' not found`);
        return;
    }
    container.innerHTML = '';
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;
    container.appendChild(alertDiv);
    setTimeout(() => alertDiv.remove(), 5000);
}

// UI Helpers
function showAlert(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;

    const container = document.querySelector('.container');
    if (container) {
        container.insertBefore(alertDiv, container.firstChild);

        // Auto-remove after 5 seconds
        setTimeout(() => {
            alertDiv.remove();
        }, 5000);
    }
}

function showError(message) {
    showAlert(message, 'error');
}

function showSuccess(message) {
    showAlert(message, 'success');
}

function formatDateTime(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString();
}

function formatDate(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString();
}

function formatTime(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleTimeString();
}

function formatStation(station) {
    const stationMap = {
        'active': 'Active',
        'sharpen': 'Sharpen',
        'cabinet': 'Cabinet',
        'refill': 'Refill'
    };
    return stationMap[station] || station;
}

function getStationBadgeClass(station) {
    return `badge-${station}`;
}

function getPriorityBadgeClass(priority) {
    if (priority >= 3) return 'badge-urgent';
    if (priority === 2) return 'badge-high';
    if (priority === 1) return 'badge-medium';
    return 'badge-low';
}

// Debounce function for preventing double-submits
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Auto-focus input after operation
function refocusInput(inputId) {
    setTimeout(() => {
        const input = document.getElementById(inputId);
        if (input) {
            input.focus();
            input.select();
        }
    }, 100);
}
