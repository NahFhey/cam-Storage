// API utility functions for CAM Tracking Kiosk

const API_BASE = '/api';

// Generic fetch wrapper with error handling
async function apiCall(endpoint, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `API error: ${response.status}`);
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

// Jobs
async function getJobs() {
    return apiCall('/jobs');
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
