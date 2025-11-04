// Student Activity Tracking System
class ActivityTracker {
    constructor(resourceId, sessionId) {
        this.resourceId = resourceId;
        this.sessionId = sessionId;
        this.startTime = Date.now();
        this.lastActivity = Date.now();
        this.isActive = true;
        this.cursorMoveCount = 0;
        this.scrollDepth = 0;
        this.clickCount = 0;
        this.focusTime = 0;
        this.idleTime = 0;
        this.lastFocusTime = Date.now();
        
        // Enhanced tracking metrics
        this.readingStartTime = null;
        this.readingProgress = 0;
        this.wordCount = 0;
        this.comprehensionChecks = [];
        this.attentionSpans = [];
        this.distractionCount = 0;
        this.returnCount = 0;
        this.lastScrollPosition = 0;
        this.scrollEvents = [];
        this.clickEvents = [];
        this.mouseMoveEvents = [];
        
        this.initializeTracking();
        this.startPeriodicUpdates();
        this.initializeReadingSpeedTracking();
    }
    
    initializeTracking() {
        // Track page load
        this.trackActivity('page_view', {
            timestamp: new Date().toISOString(),
            url: window.location.href,
            user_agent: navigator.userAgent
        });
        
        // Mouse movement tracking (throttled)
        let mouseMoveThrottle = this.throttle(() => {
            this.cursorMoveCount++;
            this.lastActivity = Date.now();
            
            // Store mouse position for pattern analysis
            this.mouseMoveEvents.push({
                x: event.clientX,
                y: event.clientY,
                timestamp: Date.now()
            });
            
            // Keep only last 100 events
            if (this.mouseMoveEvents.length > 100) {
                this.mouseMoveEvents.shift();
            }
            
            this.trackActivity('cursor_move', {
                count: this.cursorMoveCount,
                timestamp: new Date().toISOString(),
                x: event.clientX,
                y: event.clientY
            });
        }, 1000); // Track every second at most
        
        document.addEventListener('mousemove', mouseMoveThrottle);
        
        // Scroll tracking
        let scrollThrottle = this.throttle(() => {
            const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            const docHeight = document.documentElement.scrollHeight - window.innerHeight;
            const scrollPercentage = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
            
            this.scrollDepth = Math.max(this.scrollDepth, scrollPercentage);
            this.lastActivity = Date.now();
            
            // Store scroll events for pattern analysis
            this.scrollEvents.push({
                position: scrollPercentage,
                timestamp: Date.now(),
                direction: scrollTop > this.lastScrollPosition ? 'down' : 'up'
            });
            
            this.lastScrollPosition = scrollTop;
            
            // Keep only last 50 scroll events
            if (this.scrollEvents.length > 50) {
                this.scrollEvents.shift();
            }
            
            this.trackActivity('scroll', {
                scroll_percentage: scrollPercentage,
                max_scroll_depth: this.scrollDepth,
                timestamp: new Date().toISOString(),
                direction: scrollTop > this.lastScrollPosition ? 'down' : 'up'
            });
        }, 500);
        
        window.addEventListener('scroll', scrollThrottle);
        
        // Click tracking
        document.addEventListener('click', (e) => {
            this.clickCount++;
            this.lastActivity = Date.now();
            
            // Store click events for pattern analysis
            this.clickEvents.push({
                element: e.target.tagName,
                element_id: e.target.id,
                element_class: e.target.className,
                x: e.clientX,
                y: e.clientY,
                timestamp: Date.now()
            });
            
            // Keep only last 50 click events
            if (this.clickEvents.length > 50) {
                this.clickEvents.shift();
            }
            
            this.trackActivity('click', {
                element: e.target.tagName,
                element_id: e.target.id,
                element_class: e.target.className,
                x: e.clientX,
                y: e.clientY,
                count: this.clickCount,
                timestamp: new Date().toISOString()
            });
        });
        
        // Focus/Blur tracking
        window.addEventListener('focus', () => {
            this.isActive = true;
            this.lastFocusTime = Date.now();
            this.returnCount++;
            
            this.trackActivity('focus', {
                timestamp: new Date().toISOString()
            });
        });
        
        window.addEventListener('blur', () => {
            if (this.isActive) {
                const focusDuration = Math.round((Date.now() - this.lastFocusTime) / 1000);
                this.focusTime += focusDuration;
                this.distractionCount++;
                
                // Record attention span
                this.attentionSpans.push(focusDuration);
                if (this.attentionSpans.length > 20) {
                    this.attentionSpans.shift();
                }
                
                this.trackActivity('focus_time', {
                    duration: focusDuration,
                    total_focus_time: this.focusTime,
                    timestamp: new Date().toISOString()
                });
            }
            this.isActive = false;
        });
        
        // Idle detection
        this.idleTimer = setInterval(() => {
            const timeSinceLastActivity = Date.now() - this.lastActivity;
            if (timeSinceLastActivity > 30000) { // 30 seconds of inactivity
                const idleDuration = Math.round(timeSinceLastActivity / 1000);
                this.idleTime += idleDuration;
                this.trackActivity('idle_time', {
                    duration: idleDuration,
                    total_idle_time: this.idleTime,
                    timestamp: new Date().toISOString()
                });
                this.lastActivity = Date.now(); // Reset to prevent multiple idle events
            }
        }, 30000);
        
        // Track when user leaves the page
        window.addEventListener('beforeunload', () => {
            this.trackSessionEnd();
        });
        
        // Track visibility changes
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.distractionCount++;
                this.trackActivity('page_hidden', {
                    timestamp: new Date().toISOString()
                });
            } else {
                this.returnCount++;
                this.trackActivity('page_visible', {
                    timestamp: new Date().toISOString()
                });
            }
        });
        
        // Initialize video tracking
        this.initializeVideoTracking();
    }
    
    initializeVideoTracking() {
        const video = document.querySelector('video');
        if (video) {
            // Track video play events
            video.addEventListener('play', () => {
                this.trackActivity('video_play', {
                    currentTime: video.currentTime,
                    duration: video.duration,
                    timestamp: new Date().toISOString()
                });
            });
            
            // Track video pause events
            video.addEventListener('pause', () => {
                this.trackActivity('video_pause', {
                    currentTime: video.currentTime,
                    duration: video.duration,
                    timestamp: new Date().toISOString()
                });
            });
            
            // Track video progress (every 10 seconds)
            video.addEventListener('timeupdate', () => {
                if (video.currentTime % 10 < 1) { // Track every 10 seconds
                    this.trackActivity('video_progress', {
                        currentTime: video.currentTime,
                        duration: video.duration,
                        progress: (video.currentTime / video.duration) * 100,
                        timestamp: new Date().toISOString()
                    });
                }
            });
            
            // Track video completion
            video.addEventListener('ended', () => {
                this.trackActivity('video_complete', {
                    duration: video.duration,
                    timestamp: new Date().toISOString()
                });
            });
            
            // Track video seeking
            video.addEventListener('seeked', () => {
                this.trackActivity('video_seek', {
                    currentTime: video.currentTime,
                    duration: video.duration,
                    timestamp: new Date().toISOString()
                });
            });
        }
    }
    
    initializeReadingSpeedTracking() {
        // Calculate word count from text content
        this.calculateWordCount();
        
        // Start reading timer when user starts scrolling down
        let readingStarted = false;
        window.addEventListener('scroll', () => {
            const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            if (scrollTop > 100 && !readingStarted) {
                readingStarted = true;
                this.readingStartTime = Date.now();
                this.trackActivity('reading_started', {
                    timestamp: new Date().toISOString()
                });
            }
        });
        
        // Calculate reading speed periodically
        setInterval(() => {
            if (this.readingStartTime && this.wordCount > 0) {
                const readingTime = (Date.now() - this.readingStartTime) / 1000 / 60; // minutes
                const wordsPerMinute = Math.round(this.wordCount / readingTime);
                
                if (wordsPerMinute > 0 && wordsPerMinute < 1000) { // Reasonable range
                    this.trackActivity('reading_speed', {
                        wpm: wordsPerMinute,
                        words_read: this.wordCount,
                        reading_time_minutes: readingTime,
                        timestamp: new Date().toISOString()
                    });
                }
            }
        }, 60000); // Every minute
    }
    
    calculateWordCount() {
        // Get all text content from the page
        const textContent = document.body.innerText || document.body.textContent || '';
        const words = textContent.trim().split(/\s+/);
        this.wordCount = words.length;
        
        this.trackActivity('content_analysis', {
            word_count: this.wordCount,
            character_count: textContent.length,
            timestamp: new Date().toISOString()
        });
    }
    
    addComprehensionCheck(question, answer, isCorrect) {
        this.comprehensionChecks.push({
            question: question,
            answer: answer,
            is_correct: isCorrect,
            timestamp: Date.now()
        });
        
        // Calculate comprehension score
        const correctAnswers = this.comprehensionChecks.filter(check => check.is_correct).length;
        const comprehensionScore = (correctAnswers / this.comprehensionChecks.length) * 100;
        
        this.trackActivity('comprehension_check', {
            score: comprehensionScore,
            total_checks: this.comprehensionChecks.length,
            correct_answers: correctAnswers,
            timestamp: new Date().toISOString()
        });
    }
    
    startPeriodicUpdates() {
        // Send periodic time updates every 30 seconds
        this.timeUpdateInterval = setInterval(() => {
            const timeSpent = Math.round((Date.now() - this.startTime) / 1000);
            this.trackActivity('time_spent', {
                duration: 30, // 30 seconds since last update
                total_time: timeSpent,
                timestamp: new Date().toISOString()
            });
        }, 30000);
    }
    
    trackActivity(activityType, data) {
        const payload = {
            resource_id: this.resourceId,
            session_id: this.sessionId,
            activity_type: activityType,
            data: data
        };
        
        // Send to server (non-blocking)
        fetch('/api/track_activity', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCSRFToken()
            },
            body: JSON.stringify(payload)
        }).catch(error => {
            console.warn('Activity tracking failed:', error);
        });
    }
    
    trackSessionEnd() {
        const totalTime = Math.round((Date.now() - this.startTime) / 1000);
        
        // Calculate average attention span
        const avgAttentionSpan = this.attentionSpans.length > 0 ? 
            this.attentionSpans.reduce((a, b) => a + b, 0) / this.attentionSpans.length : 0;
        
        // Calculate scroll patterns
        const scrollPattern = this.analyzeScrollPattern();
        
        // Calculate click patterns
        const clickPattern = this.analyzeClickPattern();
        
        // Ensure focus time doesn't exceed session duration
        const sessionFocusTime = Math.min(this.focusTime, totalTime);
        
        // Send final session data
        const finalData = {
            total_time_spent: totalTime,
            max_scroll_depth: this.scrollDepth,
            total_cursor_movements: this.cursorMoveCount,
            total_clicks: this.clickCount,
            total_focus_time: sessionFocusTime,
            total_idle_time: this.idleTime,
            distraction_count: this.distractionCount,
            return_count: this.returnCount,
            avg_attention_span: avgAttentionSpan,
            scroll_pattern: scrollPattern,
            click_pattern: clickPattern,
            comprehension_checks: this.comprehensionChecks.length,
            timestamp: new Date().toISOString()
        };
        
        // Use sendBeacon for reliable delivery on page unload
        const payload = JSON.stringify({
            resource_id: this.resourceId,
            session_id: this.sessionId,
            activity_type: 'session_end',
            data: finalData
        });
        
        if (navigator.sendBeacon) {
            navigator.sendBeacon('/api/track_activity', payload);
        } else {
            // Fallback for older browsers
            this.trackActivity('session_end', finalData);
        }
    }
    
    analyzeScrollPattern() {
        if (this.scrollEvents.length < 2) return 'insufficient_data';
        
        const downScrolls = this.scrollEvents.filter(e => e.direction === 'down').length;
        const upScrolls = this.scrollEvents.filter(e => e.direction === 'up').length;
        const totalScrolls = this.scrollEvents.length;
        
        if (downScrolls / totalScrolls > 0.7) return 'progressive_reader';
        if (upScrolls / totalScrolls > 0.3) return 'reviewer';
        return 'balanced_reader';
    }
    
    analyzeClickPattern() {
        if (this.clickEvents.length < 5) return 'insufficient_data';
        
        const interactiveClicks = this.clickEvents.filter(e => 
            e.element === 'BUTTON' || e.element === 'A' || e.element === 'INPUT'
        ).length;
        
        const totalClicks = this.clickEvents.length;
        
        if (interactiveClicks / totalClicks > 0.5) return 'interactive_learner';
        return 'passive_reader';
    }
    
    getCSRFToken() {
        const token = document.querySelector('meta[name="csrf-token"]');
        return token ? token.getAttribute('content') : '';
    }
    
    throttle(func, limit) {
        let inThrottle;
        return function() {
            const args = arguments;
            const context = this;
            if (!inThrottle) {
                func.apply(context, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        }
    }
    
    destroy() {
        // Clean up intervals and event listeners
        if (this.timeUpdateInterval) {
            clearInterval(this.timeUpdateInterval);
        }
        if (this.idleTimer) {
            clearInterval(this.idleTimer);
        }
        this.trackSessionEnd();
    }
}

// Auto-initialize tracking when script loads
window.ActivityTracker = ActivityTracker;

// Initialize tracking if resource and session data are available
document.addEventListener('DOMContentLoaded', function() {
    const resourceId = window.RESOURCE_ID;
    const sessionId = window.SESSION_ID;
    
    if (resourceId) {
        window.activityTracker = new ActivityTracker(resourceId, sessionId);
        
        // Ensure cleanup on page unload
        window.addEventListener('beforeunload', () => {
            if (window.activityTracker) {
                window.activityTracker.destroy();
            }
        });
    }
});
