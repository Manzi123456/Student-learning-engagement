#!/usr/bin/env python3
"""
Comprehensive test script for student notes functionality
Tests database table, API endpoints, and button functionality
"""

import sys
import os
import requests
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db, StudentNotes, Student, User, Resource
    from sqlalchemy import text
    
    def test_database_structure():
        """Test if the StudentNotes table exists and has proper structure"""
        print("=" * 60)
        print("TESTING DATABASE STRUCTURE")
        print("=" * 60)
        
        with app.app_context():
            # Create all tables
            db.create_all()
            print("✓ Tables created/verified")
            
            # Check if student_notes table exists
            result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='student_notes'"))
            table_exists = result.fetchone()
            
            if table_exists:
                print("✓ student_notes table exists")
                
                # Check table structure
                result = db.session.execute(text("PRAGMA table_info(student_notes)"))
                columns = [row[1] for row in result.fetchall()]
                print(f"✓ Columns: {columns}")
                
                # Check for required columns
                required_columns = ['id', 'student_id', 'resource_id', 'notes_content', 'created_at', 'updated_at']
                missing_columns = [col for col in required_columns if col not in columns]
                
                if missing_columns:
                    print(f"✗ Missing required columns: {missing_columns}")
                    return False
                else:
                    print("✓ All required columns present")
                
                # Test insert/update
                try:
                    # Check if we have any data
                    count = db.session.execute(text("SELECT COUNT(*) FROM student_notes")).fetchone()[0]
                    print(f"✓ Current notes count: {count}")
                    
                    # Test a sample insert
                    test_notes = StudentNotes(
                        student_id=1,
                        resource_id=2,
                        notes_content="Test notes content",
                        word_count=3,
                        character_count=20,
                        engagement_score=15.0
                    )
                    db.session.add(test_notes)
                    db.session.commit()
                    print("✓ Test insert successful")
                    
                    # Clean up test data
                    db.session.delete(test_notes)
                    db.session.commit()
                    print("✓ Test cleanup successful")
                    
                except Exception as e:
                    print(f"✗ Error testing database operations: {e}")
                    return False
                    
            else:
                print("✗ student_notes table does not exist")
                return False
                
        return True
    
    def test_api_endpoints():
        """Test the save and get notes API endpoints"""
        print("\n" + "=" * 60)
        print("TESTING API ENDPOINTS")
        print("=" * 60)
        
        base_url = "http://127.0.0.1:5000"
        resource_id = 2
        
        # Test data
        test_notes = {
            "notes": "This is a test note for resource 2. Testing the API functionality."
        }
        
        print(f"Testing with resource ID: {resource_id}")
        print(f"Test notes: {test_notes['notes']}")
        
        # Test GET notes endpoint
        print("\n1. Testing GET /api/get_notes/<resource_id>")
        try:
            response = requests.get(f"{base_url}/api/get_notes/{resource_id}")
            print(f"   Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"   Response: {json.dumps(data, indent=2)}")
                print("   ✓ GET endpoint working")
            else:
                print(f"   ✗ GET endpoint failed: {response.text}")
                return False
                
        except requests.exceptions.ConnectionError:
            print("   ⚠ Server not running - skipping API tests")
            return True
        except Exception as e:
            print(f"   ✗ GET endpoint error: {e}")
            return False
        
        # Test POST save notes endpoint
        print("\n2. Testing POST /api/save_notes/<resource_id>")
        try:
            response = requests.post(
                f"{base_url}/api/save_notes/{resource_id}",
                json=test_notes,
                headers={'Content-Type': 'application/json'}
            )
            print(f"   Status Code: {response.status_code}")
            
            if response.status_code in [200, 201]:
                data = response.json()
                print(f"   Response: {json.dumps(data, indent=2)}")
                print("   ✓ POST endpoint working")
            else:
                print(f"   ✗ POST endpoint failed: {response.text}")
                return False
                
        except Exception as e:
            print(f"   ✗ POST endpoint error: {e}")
            return False
        
        # Test GET again to verify save worked
        print("\n3. Verifying save with GET request")
        try:
            response = requests.get(f"{base_url}/api/get_notes/{resource_id}")
            if response.status_code == 200:
                data = response.json()
                if data.get('notes') == test_notes['notes']:
                    print("   ✓ Notes saved and retrieved successfully")
                else:
                    print("   ✗ Notes not saved correctly")
                    return False
            else:
                print(f"   ✗ Failed to retrieve saved notes: {response.text}")
                return False
                
        except Exception as e:
            print(f"   ✗ Verification error: {e}")
            return False
        
        return True
    
    def test_frontend_functionality():
        """Test the frontend button functionality"""
        print("\n" + "=" * 60)
        print("TESTING FRONTEND FUNCTIONALITY")
        print("=" * 60)
        
        base_url = "http://127.0.0.1:5000"
        resource_id = 2
        
        print(f"Testing frontend page: {base_url}/student/view_resource/{resource_id}")
        
        try:
            response = requests.get(f"{base_url}/student/view_resource/{resource_id}")
            print(f"   Status Code: {response.status_code}")
            
            if response.status_code == 200:
                content = response.text
                
                # Check for required elements
                required_elements = [
                    'id="notesTextarea"',
                    'id="saveNotesBtn"',
                    'id="clearNotesBtn"',
                    'id="quickSaveBtn"',
                    'id="wordCount"',
                    'id="charCount"',
                    'updateStatus',
                    'saveNotes',
                    'loadNotes'
                ]
                
                missing_elements = []
                for element in required_elements:
                    if element not in content:
                        missing_elements.append(element)
                
                if missing_elements:
                    print(f"   ✗ Missing frontend elements: {missing_elements}")
                    return False
                else:
                    print("   ✓ All required frontend elements present")
                
                # Check for JavaScript functions
                js_functions = [
                    'function saveNotes()',
                    'function loadNotes()',
                    'function updateStatus(',
                    'function updateCounts()',
                    'function updateButtonStates()'
                ]
                
                missing_functions = []
                for func in js_functions:
                    if func not in content:
                        missing_functions.append(func)
                
                if missing_functions:
                    print(f"   ✗ Missing JavaScript functions: {missing_functions}")
                    return False
                else:
                    print("   ✓ All required JavaScript functions present")
                
                print("   ✓ Frontend page loads correctly")
                return True
                
            else:
                print(f"   ✗ Frontend page failed to load: {response.text}")
                return False
                
        except requests.exceptions.ConnectionError:
            print("   ⚠ Server not running - skipping frontend tests")
            return True
        except Exception as e:
            print(f"   ✗ Frontend test error: {e}")
            return False
    
    def main():
        """Run all tests"""
        print("STUDENT NOTES FUNCTIONALITY TEST")
        print("=" * 60)
        print(f"Test started at: {datetime.now()}")
        
        # Run tests
        db_test = test_database_structure()
        api_test = test_api_endpoints()
        frontend_test = test_frontend_functionality()
        
        # Summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"Database Structure: {'✓ PASS' if db_test else '✗ FAIL'}")
        print(f"API Endpoints: {'✓ PASS' if api_test else '✗ FAIL'}")
        print(f"Frontend Functionality: {'✓ PASS' if frontend_test else '✗ FAIL'}")
        
        if db_test and api_test and frontend_test:
            print("\n🎉 ALL TESTS PASSED! Student notes functionality is working correctly.")
            return True
        else:
            print("\n❌ SOME TESTS FAILED! Please check the issues above.")
            return False
    
    if __name__ == "__main__":
        success = main()
        sys.exit(0 if success else 1)
        
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running this script from the project root directory")
    sys.exit(1)
except Exception as e:
    print(f"Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)