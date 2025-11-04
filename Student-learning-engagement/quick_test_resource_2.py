#!/usr/bin/env python3
"""
Quick test for notes functionality on resource 2
"""

import requests
import time

def test_resource_2():
    base_url = "http://127.0.0.1:5000"
    resource_id = 2
    
    print(f"Testing notes functionality on resource {resource_id}")
    print(f"URL: {base_url}/student/view_resource/{resource_id}")
    
    try:
        # Test the page
        response = requests.get(f"{base_url}/student/view_resource/{resource_id}")
        print(f"Page status: {response.status_code}")
        
        if response.status_code == 200:
            print("✓ Page loads successfully")
            
            # Check for notes elements
            html_content = response.text
            checks = [
                ('notesTextarea', 'Notes textarea'),
                ('saveNotesBtn', 'Save button'),
                ('clearNotesBtn', 'Clear button'),
                ('notesStatus', 'Status element'),
                ('initNotes()', 'Notes initialization'),
                ('scheduleAutoSave', 'Auto-save function'),
                ('updateStatus', 'Status update function')
            ]
            
            for element, name in checks:
                if element in html_content:
                    print(f"✓ {name} found")
                else:
                    print(f"✗ {name} missing")
        
        # Test API endpoints
        print("\nTesting API endpoints:")
        
        # Test get notes
        try:
            api_response = requests.get(f"{base_url}/api/get_notes/{resource_id}")
            print(f"GET /api/get_notes/{resource_id}: {api_response.status_code}")
            if api_response.status_code == 200:
                data = api_response.json()
                print(f"Response: {data}")
        except Exception as e:
            print(f"API test error: {e}")
            
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n" + "="*50)
    print("MANUAL TEST INSTRUCTIONS:")
    print("="*50)
    print(f"1. Open browser: {base_url}/student/view_resource/{resource_id}")
    print("2. Login as a student")
    print("3. Look for the 'My Notes' section on the right")
    print("4. Type in the notes textarea")
    print("5. Watch for status messages:")
    print("   - 'Typing… auto-save in 3s'")
    print("   - 'Saving...'")
    print("   - 'Saved successfully'")
    print("6. Click Save button - should show spinner")
    print("7. Click Clear button - should show confirmation")
    print("8. Check browser console for any errors")

if __name__ == "__main__":
    test_resource_2()


