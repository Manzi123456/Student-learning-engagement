#!/usr/bin/env python3
"""
Final verification of notes functionality on resource 2
"""

import requests
import json

def verify_notes_functionality():
    base_url = "http://127.0.0.1:5000"
    resource_id = 2
    
    print("="*60)
    print("NOTES FUNCTIONALITY VERIFICATION - RESOURCE 2")
    print("="*60)
    print(f"Testing URL: {base_url}/student/view_resource/{resource_id}")
    
    try:
        # Test 1: Page loads
        print("\n1. Testing page load...")
        response = requests.get(f"{base_url}/student/view_resource/{resource_id}")
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("   ✓ Page loads successfully")
            
            # Check HTML content
            html = response.text
            
            # Check for essential elements
            elements = {
                'notesTextarea': 'Notes textarea',
                'saveNotesBtn': 'Save button', 
                'clearNotesBtn': 'Clear button',
                'notesStatus': 'Status display',
                'notesTimestamp': 'Timestamp display',
                'initNotes()': 'Notes initialization function',
                'scheduleAutoSave': 'Auto-save function',
                'updateStatus': 'Status update function',
                'getCsrfToken': 'CSRF token function'
            }
            
            print("\n2. Checking HTML elements...")
            for element, description in elements.items():
                if element in html:
                    print(f"   ✓ {description}")
                else:
                    print(f"   ✗ {description} - MISSING")
            
            # Check for auto-save message
            if 'auto-save in 3s' in html:
                print("   ✓ Auto-save message found")
            else:
                print("   ✗ Auto-save message missing")
                
        else:
            print(f"   ✗ Page failed to load: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"   ✗ Error loading page: {e}")
        return False
    
    # Test 2: API endpoints
    print("\n3. Testing API endpoints...")
    
    # Test get notes API
    try:
        api_response = requests.get(f"{base_url}/api/get_notes/{resource_id}")
        print(f"   GET /api/get_notes/{resource_id}: {api_response.status_code}")
        
        if api_response.status_code == 200:
            data = api_response.json()
            print(f"   Response: {json.dumps(data, indent=2)}")
            if data.get('success'):
                print("   ✓ Get notes API working")
            else:
                print(f"   ✗ Get notes API error: {data.get('error')}")
        else:
            print(f"   ✗ Get notes API failed: {api_response.status_code}")
            
    except Exception as e:
        print(f"   ✗ Get notes API error: {e}")
    
    # Test save notes API (will fail due to CSRF, but we can see the response)
    try:
        test_data = {'notes': 'Test notes for verification'}
        save_response = requests.post(f"{base_url}/api/save_notes/{resource_id}", 
                                    json=test_data)
        print(f"   POST /api/save_notes/{resource_id}: {save_response.status_code}")
        
        if save_response.status_code == 400:
            data = save_response.json()
            if 'CSRF' in str(data):
                print("   ✓ CSRF protection working (expected)")
            else:
                print(f"   Response: {data}")
        else:
            print(f"   Response: {save_response.text[:100]}...")
            
    except Exception as e:
        print(f"   ✗ Save notes API error: {e}")
    
    print("\n" + "="*60)
    print("MANUAL TESTING INSTRUCTIONS")
    print("="*60)
    print(f"1. Open browser: {base_url}/student/view_resource/{resource_id}")
    print("2. Login as a student")
    print("3. Look for 'My Notes' section on the right side")
    print("4. Type in the notes textarea")
    print("5. Expected behavior:")
    print("   - Status shows: 'Typing… auto-save in 3s'")
    print("   - After 3 seconds: 'Saving...'")
    print("   - Then: 'Saved successfully'")
    print("   - Timestamp updates")
    print("6. Click Save button:")
    print("   - Button shows spinner")
    print("   - Status shows 'Saving...'")
    print("   - Then 'Saved successfully'")
    print("7. Click Clear button:")
    print("   - Confirmation dialog appears")
    print("   - After confirm: 'Clearing...'")
    print("   - Then 'Notes cleared'")
    print("8. Check browser console (F12) for any errors")
    
    print("\n" + "="*60)
    print("TEACHER VIEWING NOTES")
    print("="*60)
    print(f"Teachers can view student notes at:")
    print(f"{base_url}/teacher/view_student_notes/{resource_id}")
    print("This shows all student notes for grading.")
    
    return True

if __name__ == "__main__":
    verify_notes_functionality()


