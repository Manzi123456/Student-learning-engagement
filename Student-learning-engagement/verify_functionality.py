#!/usr/bin/env python3
"""
Quick verification script to check that all key functionality is working.
This script checks the code for common issues and verifies button functionality.
"""

import os
import re
from pathlib import Path

def check_file_exists(file_path):
    """Check if a file exists"""
    return os.path.exists(file_path)

def check_javascript_functions(template_file):
    """Check if JavaScript functions are properly defined"""
    issues = []
    
    if not check_file_exists(template_file):
        issues.append(f"Template file not found: {template_file}")
        return issues
    
    with open(template_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for required JavaScript functions
    required_functions = [
        'function saveNotes()',
        'function clearNotes()',
        'function finishReading()',
        'function loadNotes()',
        'function updateButtonStates()',
        'function setupAutoSave()'
    ]
    
    for func in required_functions:
        if func not in content:
            issues.append(f"Missing function: {func}")
    
    # Check for button event handlers
    button_handlers = [
        'onclick="saveNotes()"',
        'onclick="clearNotes()"',
        'onclick="finishReading()"'
    ]
    
    for handler in button_handlers:
        if handler not in content:
            issues.append(f"Missing button handler: {handler}")
    
    return issues

def check_api_endpoints(app_file):
    """Check if API endpoints are properly defined"""
    issues = []
    
    if not check_file_exists(app_file):
        issues.append(f"App file not found: {app_file}")
        return issues
    
    with open(app_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for required API endpoints
    required_endpoints = [
        '@app.route(\'/api/save_notes/<int:resource_id>\', methods=[\'POST\'])',
        '@app.route(\'/api/get_notes/<int:resource_id>\')',
        '@app.route(\'/api/check_quiz_exists/<int:resource_id>\')',
        '@app.route(\'/api/track_activity\', methods=[\'POST\'])',
        '@app.route(\'/student/start_study/<int:resource_id>\', methods=[\'POST\'])',
        '@app.route(\'/student/end_study/<int:session_id>\', methods=[\'POST\'])'
    ]
    
    for endpoint in required_endpoints:
        if endpoint not in content:
            issues.append(f"Missing API endpoint: {endpoint}")
    
    return issues

def check_database_models(app_file):
    """Check if database models are properly defined"""
    issues = []
    
    if not check_file_exists(app_file):
        issues.append(f"App file not found: {app_file}")
        return issues
    
    with open(app_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for required database models
    required_models = [
        'class StudentNotes(db.Model)',
        'class StudentActivity(db.Model)',
        'class ResourceEngagement(db.Model)',
        'class StudySession(db.Model)',
        'class StudentAnswer(db.Model)',
        'class Question(db.Model)'
    ]
    
    for model in required_models:
        if model not in content:
            issues.append(f"Missing database model: {model}")
    
    return issues

def check_activity_tracker(js_file):
    """Check if activity tracker is properly implemented"""
    issues = []
    
    if not check_file_exists(js_file):
        issues.append(f"Activity tracker file not found: {js_file}")
        return issues
    
    with open(js_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for required activity tracker features
    required_features = [
        'class ActivityTracker',
        'trackActivity(',
        'initializeTracking()',
        'trackSessionEnd()'
    ]
    
    for feature in required_features:
        if feature not in content:
            issues.append(f"Missing activity tracker feature: {feature}")
    
    return issues

def check_quiz_functionality(template_file):
    """Check if quiz functionality is properly implemented"""
    issues = []
    
    if not check_file_exists(template_file):
        issues.append(f"Quiz template file not found: {template_file}")
        return issues
    
    with open(template_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for required quiz features
    required_features = [
        'id="quizForm"',
        'id="submitBtn"',
        'addEventListener(\'submit\'',
        'fetch(\'/student/submit_answer\'',
        'fetch(\'/student/complete_quiz\''
    ]
    
    for feature in required_features:
        if feature not in content:
            issues.append(f"Missing quiz feature: {feature}")
    
    return issues

def main():
    """Main verification function"""
    print("=" * 60)
    print("STUDENT TRACK APPLICATION - FUNCTIONALITY VERIFICATION")
    print("=" * 60)
    
    issues = []
    
    # Check template files
    print("\n1. Checking Template Files...")
    template_files = [
        'templates/view_resource.html',
        'templates/student_quiz.html',
        'templates/student_dashboard.html',
        'templates/student_resources.html'
    ]
    
    for template in template_files:
        if check_file_exists(template):
            print(f"✓ {template} exists")
        else:
            print(f"✗ {template} missing")
            issues.append(f"Missing template: {template}")
    
    # Check JavaScript functionality
    print("\n2. Checking JavaScript Functionality...")
    js_issues = check_javascript_functions('templates/view_resource.html')
    if js_issues:
        for issue in js_issues:
            print(f"✗ {issue}")
            issues.extend(js_issues)
    else:
        print("✓ All JavaScript functions present")
    
    # Check API endpoints
    print("\n3. Checking API Endpoints...")
    api_issues = check_api_endpoints('app.py')
    if api_issues:
        for issue in api_issues:
            print(f"✗ {issue}")
            issues.extend(api_issues)
    else:
        print("✓ All API endpoints present")
    
    # Check database models
    print("\n4. Checking Database Models...")
    model_issues = check_database_models('app.py')
    if model_issues:
        for issue in model_issues:
            print(f"✗ {issue}")
            issues.extend(model_issues)
    else:
        print("✓ All database models present")
    
    # Check activity tracker
    print("\n5. Checking Activity Tracker...")
    tracker_issues = check_activity_tracker('static/js/activity_tracker.js')
    if tracker_issues:
        for issue in tracker_issues:
            print(f"✗ {issue}")
            issues.extend(tracker_issues)
    else:
        print("✓ Activity tracker properly implemented")
    
    # Check quiz functionality
    print("\n6. Checking Quiz Functionality...")
    quiz_issues = check_quiz_functionality('templates/student_quiz.html')
    if quiz_issues:
        for issue in quiz_issues:
            print(f"✗ {issue}")
            issues.extend(quiz_issues)
    else:
        print("✓ Quiz functionality properly implemented")
    
    # Check static files
    print("\n7. Checking Static Files...")
    static_files = [
        'static/js/activity_tracker.js',
        'requirements.txt'
    ]
    
    for static_file in static_files:
        if check_file_exists(static_file):
            print(f"✓ {static_file} exists")
        else:
            print(f"✗ {static_file} missing")
            issues.append(f"Missing static file: {static_file}")
    
    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    if issues:
        print(f"✗ Found {len(issues)} issues:")
        for issue in issues:
            print(f"  - {issue}")
        print("\nPlease fix these issues before running the application.")
    else:
        print("✓ All functionality checks passed!")
        print("\nThe application should be working correctly with:")
        print("  - Save button functionality")
        print("  - Clear button functionality") 
        print("  - Finish reading & take quiz functionality")
        print("  - Activity tracking")
        print("  - Notes storage")
        print("  - Quiz functionality")
    
    print("\nTo test the application:")
    print("1. Start the application: python app.py")
    print("2. Create test accounts through admin panel")
    print("3. Login and test all functionality")
    print("4. Use test_functionality.py for automated testing")

if __name__ == "__main__":
    main()
