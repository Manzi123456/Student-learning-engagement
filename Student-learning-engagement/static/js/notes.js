// Notes Module - Handles all note-taking functionality
class NotesManager {
    constructor() {
        this.notesChanged = false;
        this.autoSaveTimer = null;
        this.isSaving = false;
        this.noteVersions = [];
        this.currentVersionIndex = -1;
        this.isOffline = !navigator.onLine;
        this.offlineNotes = [];
        this.autoSaveEnabled = true;
        
        // Initialize when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.init());
        } else {
            this.init();
        }
    }

    // Initialize notes functionality
    init() {
        console.log('Initializing Notes Manager...');
        
        // Set up event listeners
        this.setupEventListeners();
        
        // Load existing notes
        this.loadNotes();
        
        // Set up auto-save interval (every 2 minutes)
        setInterval(() => this.autoSave(), 120000);
        
        // Handle online/offline status
        this.setupConnectivityHandlers();
    }

    // Set up event listeners
    setupEventListeners() {
        const textarea = document.getElementById('notesTextarea');
        const saveBtn = document.getElementById('saveNotesBtn');
        const clearBtn = document.getElementById('clearNotesBtn');
        const quickSaveBtn = document.getElementById('quickSaveBtn');
        const autoSaveToggle = document.getElementById('autoSaveToggle');

        // Textarea events
        if (textarea) {
            textarea.addEventListener('input', () => this.handleInput());
            textarea.addEventListener('blur', () => this.handleBlur());
        }

        // Button events
        if (saveBtn) saveBtn.addEventListener('click', (e) => {
            e.preventDefault();
            this.saveNotes(true);
        });

        if (clearBtn) clearBtn.addEventListener('click', (e) => {
            e.preventDefault();
            this.clearNotes();
        });

        if (quickSaveBtn) {
            quickSaveBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.saveNotes(true);
            });
        }

        // Auto-save toggle
        if (autoSaveToggle) {
            autoSaveToggle.addEventListener('change', (e) => {
                this.autoSaveEnabled = e.target.checked;
                this.updateStatus(
                    `Auto-save ${this.autoSaveEnabled ? 'enabled' : 'disabled'}`,
                    this.autoSaveEnabled ? 'success' : 'warning'
                );
            });
        }

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
            const ctrlKey = isMac ? e.metaKey : e.ctrlKey;
            
            // Save: Ctrl/Cmd + S
            if (ctrlKey && e.key === 's') {
                e.preventDefault();
                this.saveNotes(true);
            }
            
            // New note: Ctrl/Cmd + N
            if (ctrlKey && e.key === 'n') {
                e.preventDefault();
                this.clearNotes();
            }
        });

        // Save before page unload
        window.addEventListener('beforeunload', () => this.handleBeforeUnload());
    }

    // Handle text input
    handleInput() {
        this.notesChanged = true;
        this.updateButtonStates();
        this.scheduleAutoSave();
        this.updateCounts();
    }

    // Handle textarea blur
    handleBlur() {
        const textarea = document.getElementById('notesTextarea');
        if (textarea?.value.trim() && this.notesChanged) {
            this.saveNotes();
        }
    }

    // Schedule auto-save with debounce
    scheduleAutoSave() {
        if (!this.autoSaveEnabled) return;
        
        if (this.autoSaveTimer) {
            clearTimeout(this.autoSaveTimer);
        }
        
        this.updateStatus('Auto-saving soon...', 'info');
        
        this.autoSaveTimer = setTimeout(() => {
            const textarea = document.getElementById('notesTextarea');
            if (textarea?.value.trim() && !this.isSaving) {
                this.saveNotes();
            }
        }, 3000); // 3 second debounce
    }

    // Auto-save function
    async autoSave() {
        const textarea = document.getElementById('notesTextarea');
        if (textarea?.value.trim() && !this.isSaving && this.notesChanged) {
            await this.saveNotes();
        }
    }

    // Update word and character counts
    updateCounts() {
        const textarea = document.getElementById('notesTextarea');
        const wordCountEl = document.getElementById('wordCount');
        const charCountEl = document.getElementById('charCount');
        
        if (!textarea) return;
        
        const text = textarea.value;
        const words = text.trim() ? text.trim().split(/\s+/).length : 0;
        const chars = text.length;
        
        if (wordCountEl) wordCountEl.textContent = words;
        if (charCountEl) charCountEl.textContent = chars;
        
        return { words, chars };
    }

    // Update button states
    updateButtonStates() {
        const textarea = document.getElementById('notesTextarea');
        const saveBtn = document.getElementById('saveNotesBtn');
        const clearBtn = document.getElementById('clearNotesBtn');
        const quickSaveBtn = document.getElementById('quickSaveBtn');
        
        if (!textarea || !saveBtn || !clearBtn) return;
        
        const hasContent = textarea.value.trim().length > 0;
        
        // Save button
        saveBtn.disabled = !hasContent || this.isSaving;
        saveBtn.className = `btn btn-sm btn-${hasContent && !this.isSaving ? 'primary' : 'outline-secondary'}`;
        
        // Clear button
        clearBtn.disabled = !hasContent || this.isSaving;
        clearBtn.className = `btn btn-sm btn-${hasContent && !this.isSaving ? 'outline-danger' : 'outline-secondary'}`;
        
        // Quick save button (floating action button)
        if (quickSaveBtn) {
            quickSaveBtn.style.display = hasContent && this.notesChanged ? 'block' : 'none';
            quickSaveBtn.disabled = this.isSaving;
        }
    }

    // Update status message
    updateStatus(message, type = 'info') {
        const statusEl = document.getElementById('statusMessage');
        if (!statusEl) return;
        
        const icons = {
            success: 'check-circle',
            error: 'exclamation-triangle',
            warning: 'exclamation-circle',
            info: 'info-circle',
            saving: 'spinner fa-spin'
        };
        
        const icon = icons[type] || 'info-circle';
        const className = `alert alert-${type} d-flex align-items-center`;
        
        statusEl.innerHTML = `
            <i class="fas ${icon} me-2"></i>
            <span>${message}</span>
        `;
        statusEl.className = className;
        
        // Auto-hide non-error messages after 3 seconds
        if (type !== 'error' && type !== 'saving') {
            setTimeout(() => {
                if (statusEl.textContent.includes(message)) {
                    statusEl.className = 'd-none';
                }
            }, 3000);
        } else {
            statusEl.className = className;
        }
    }

    // Update last saved timestamp
    updateTimestamp(timestamp) {
        const timestampEl = document.getElementById('notesTimestamp');
        if (!timestampEl) return;
        
        try {
            const date = new Date(timestamp);
            timestampEl.textContent = `Last saved: ${date.toLocaleString()}`;
        } catch (e) {
            console.error('Error formatting timestamp:', e);
        }
    }

    // Handle beforeunload event
    handleBeforeUnload() {
        const textarea = document.getElementById('notesTextarea');
        if (textarea?.value.trim() && this.notesChanged && !this.isSaving) {
            // Use synchronous XHR for reliable save on page unload
            const xhr = new XMLHttpRequest();
            xhr.open('POST', `/api/save_notes/${window.RESOURCE_ID}`, false);
            xhr.setRequestHeader('Content-Type', 'application/json');
            
            const csrf = this.getCsrfToken();
            if (csrf) xhr.setRequestHeader('X-CSRFToken', csrf);
            
            try {
                xhr.send(JSON.stringify({ 
                    notes: textarea.value.trim(),
                    is_autosave: true
                }));
            } catch (e) {
                console.warn('Failed to save on unload:', e);
            }
        }
    }

    // Get CSRF token
    getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    // Set up online/offline handlers
    setupConnectivityHandlers() {
        window.addEventListener('online', () => {
            this.isOffline = false;
            this.updateStatus('Back online. Syncing changes...', 'success');
            this.syncOfflineNotes();
        });

        window.addEventListener('offline', () => {
            this.isOffline = true;
            this.updateStatus('Offline - changes will save when back online', 'warning');
        });
    }

    // Sync offline notes when back online
    async syncOfflineNotes() {
        if (this.offlineNotes.length === 0 || !navigator.onLine) return;
        
        this.updateStatus('Syncing offline changes...', 'info');
        
        try {
            while (this.offlineNotes.length > 0) {
                const note = this.offlineNotes.shift();
                await this.saveNoteToServer(note.content, note.timestamp);
            }
            this.updateStatus('All changes synced!', 'success');
        } catch (error) {
            console.error('Error syncing offline notes:', error);
            this.updateStatus('Error syncing some changes', 'error');
        }
    }

    // Save notes to server
    async saveNotes(manualSave = false) {
        const textarea = document.getElementById('notesTextarea');
        if (!window.RESOURCE_ID || !textarea) {
            this.updateStatus('Error: Cannot save notes', 'error');
            return;
        }
        
        if (this.isSaving) {
            if (manualSave) this.updateStatus('Saving in progress...', 'info');
            return;
        }
        
        const notesContent = textarea.value.trim();
        
        try {
            this.isSaving = true;
            this.updateButtonStates();
            
            if (manualSave) {
                this.updateStatus('Saving...', 'saving');
            }
            
            // Save to server or queue for offline
            if (navigator.onLine) {
                await this.saveNoteToServer(notesContent);
            } else {
                this.offlineNotes.push({
                    content: notesContent,
                    timestamp: new Date().toISOString()
                });
                this.updateStatus('Offline - changes will sync when back online', 'warning');
            }
            
            // Update UI
            this.notesChanged = false;
            this.updateTimestamp(new Date().toISOString());
            
            if (manualSave) {
                this.updateStatus('Notes saved successfully!', 'success');
            }
            
        } catch (error) {
            console.error('Save error:', error);
            this.updateStatus(`Save failed: ${error.message}`, 'error');
        } finally {
            this.isSaving = false;
            this.updateButtonStates();
        }
    }

    // Save note to server (internal method)
    async saveNoteToServer(content, timestamp = null) {
        const response = await fetch(`/api/save_notes/${window.RESOURCE_ID}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCsrfToken()
            },
            body: JSON.stringify({
                notes: content,
                timestamp: timestamp || new Date().toISOString()
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    }

    // Clear notes
    async clearNotes() {
        if (!confirm('Are you sure you want to clear all notes? This cannot be undone.')) {
            return;
        }
        
        const textarea = document.getElementById('notesTextarea');
        if (!textarea) return;
        
        try {
            this.updateStatus('Clearing notes...', 'warning');
            
            // Clear locally
            textarea.value = '';
            this.notesChanged = true;
            this.updateCounts();
            
            // Clear on server if online
            if (navigator.onLine) {
                await this.saveNoteToServer('');
            } else {
                this.offlineNotes.push({
                    content: '',
                    timestamp: new Date().toISOString(),
                    isClear: true
                });
            }
            
            this.updateStatus('Notes cleared', 'success');
            this.updateTimestamp(new Date().toISOString());
            
        } catch (error) {
            console.error('Error clearing notes:', error);
            this.updateStatus('Failed to clear notes', 'error');
        } finally {
            this.updateButtonStates();
        }
    }

    // Load notes from server
    async loadNotes() {
        if (!window.RESOURCE_ID) {
            console.error('No resource ID available');
            return;
        }
        
        try {
            this.updateStatus('Loading notes...', 'info');
            
            const response = await fetch(`/api/get_notes/${window.RESOURCE_ID}`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                const textarea = document.getElementById('notesTextarea');
                if (textarea) {
                    textarea.value = data.notes || '';
                    this.updateCounts();
                    
                    if (data.last_updated || data.updated_at) {
                        this.updateTimestamp(data.last_updated || data.updated_at);
                    }
                    
                    if (data.notes && data.notes.trim()) {
                        this.updateStatus('Notes loaded', 'success');
                    } else {
                        this.updateStatus('Start taking notes...', 'info');
                    }
                    
                    this.notesChanged = false;
                    this.updateButtonStates();
                }
            } else {
                throw new Error(data.error || 'Failed to load notes');
            }
            
        } catch (error) {
            console.error('Error loading notes:', error);
            this.updateStatus('Failed to load notes', 'error');
        }
    }
}

// Initialize Notes Manager when the script loads
const notesManager = new NotesManager();

// Make it available globally if needed
window.NotesManager = NotesManager;
