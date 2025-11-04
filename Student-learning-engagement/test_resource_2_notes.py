#!/usr/bin/env python3
"""
Test script to verify notes functionality on resource ID 2
"""

import requests
import json
import time

def test_notes_on_resource_2():
    """Test notes functionality on http://127.0.0.1:5000/student/view_resource/2"""
    base_url = "http://127.0.0.1:5000"
    resource_id = 2
    
    print(f"Testing notes functionality on resource {resource_id}")
    print(f"URL: {base_url}/student/view_resource/{resource_id}")
    
    # Test 1: Check if the page loads
    print("\n1. Testing page load...")
    try:
        response = requests.get(f"{base_url}/student/view_resource/{resource_id}")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✓ Page loads successfully")
            
            # Check if notes elements are present
            if 'notesTextarea' in response.text:
                print("✓ Notes textarea found in HTML")
            else:
                print("✗ Notes textarea not found in HTML")
                
            if 'saveNotesBtn' in response.text:
                print("✓ Save button found in HTML")
            else:
                print("✗ Save button not found in HTML")
                
            if 'clearNotesBtn' in response.text:
                print("✓ Clear button found in HTML")
            else:
                print("✗ Clear button not found in HTML")
                
            if 'notesStatus' in response.text:
                print("✓ Status element found in HTML")
            else:
                print("✗ Status element not found in HTML")
                
        else:
            print(f"✗ Page failed to load: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"✗ Error loading page: {e}")
        return False
    
    # Test 2: Test notes API endpoints
    print("\n2. Testing notes API endpoints...")
    
    # Test get notes
    try:
        response = requests.get(f"{base_url}/api/get_notes/{resource_id}")
        print(f"GET /api/get_notes/{resource_id} - Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response: {data}")
            if data.get('success'):
                print("✓ Get notes API working")
            else:
                print(f"✗ Get notes API failed: {data.get('error')}")
        else:
            print(f"✗ Get notes API failed with status: {response.status_code}")
            
    except Exception as e:
        print(f"✗ Error testing get notes API: {e}")
    
    # Test save notes (this will likely fail due to CSRF, but we can see the response)
    try:
        test_notes = "Test notes for resource 2 functionality verification"
        response = requests.post(f"{base_url}/api/save_notes/{resource_id}", 
                               json={'notes': test_notes})
        print(f"POST /api/save_notes/{resource_id} - Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Response: {data}")
            if data.get('success'):
                print("✓ Save notes API working")
            else:
                print(f"✗ Save notes API failed: {data.get('error')}")
        elif response.status_code == 400:
            data = response.json()
            if 'CSRF' in str(data):
                print("✓ CSRF protection working (expected for direct API test)")
            else:
                print(f"✗ Unexpected error: {data}")
        else:
            print(f"✗ Save notes API failed with status: {response.status_code}")
            
    except Exception as e:
        print(f"✗ Error testing save notes API: {e}")
    
    print("\n" + "="*60)
    print("TEST COMPLETED")
    print("="*60)
    print("\nTo test manually:")
    print(f"1. Open browser: {base_url}/student/view_resource/{resource_id}")
    print("2. Login as a student")
    print("3. Type in the notes area")
    print("4. Watch for auto-save status messages")
    print("5. Click Save and Clear buttons")
    
    return True

if __name__ == "__main__":
    print("="*60)
    print("TESTING NOTES FUNCTIONALITY ON RESOURCE 2")
    print("="*60)
    
    # Wait a moment for the server to start
    print("Waiting for server to start...")
    time.sleep(3)
    
    test_notes_on_resource_2()


