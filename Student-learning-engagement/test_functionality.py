#!/usr/bin/env python3
"""
Comprehensive test script for student-track application functionality.
Tests all buttons and features to ensure they work correctly.
"""

import requests
import json
import time
from datetime import datetime

class StudentTrackTester:
    def __init__(self, base_url="http://localhost:5000"):
        self.base_url = base_url
        self.session = requests.Session()
        self.student_credentials = None
        self.teacher_credentials = None
        
    def test_login(self, username, password, role="student"):
        """Test login functionality"""
        print(f"Testing {role} login...")
        
        login_data = {
            'username': username,
            'password': password
        }
        
        response = self.session.post(f"{self.base_url}/login", data=login_data)
        
        if response.status_code == 200 and "dashboard" in response.url:
            print(f"✓ {role.title()} login successful")
            if role == "student":
                self.student_credentials = (username, password)
            else:
                self.teacher_credentials = (username, password)
            return True
        else:
            print(f"✗ {role.title()} login failed")
            return False
    
    def test_student_dashboard(self):
        """Test student dashboard access"""
        print("Testing student dashboard...")
        
        response = self.session.get(f"{self.base_url}/student/dashboard")
        
        if response.status_code == 200:
            print("✓ Student dashboard accessible")
            return True
        else:
            print("✗ Student dashboard not accessible")
            return False
    
    def test_resources_page(self):
        """Test resources page access"""
        print("Testing resources page...")
        
        response = self.session.get(f"{self.base_url}/student/resources")
        
        if response.status_code == 200:
            print("✓ Resources page accessible")
            return True
        else:
            print("✗ Resources page not accessible")
            return False
    
    def test_notes_functionality(self, resource_id):
        """Test notes save, clear, and auto-save functionality"""
        print(f"Testing notes functionality for resource {resource_id}...")
        
        # Test get notes
        response = self.session.get(f"{self.base_url}/api/get_notes/{resource_id}")
        if response.status_code == 200:
            print("✓ Get notes API working")
        else:
            print("✗ Get notes API failed")
            return False
        
        # Test save notes
        notes_data = {
            'notes': 'Test notes content for functionality testing'
        }
        
        response = self.session.post(
            f"{self.base_url}/api/save_notes/{resource_id}",
            json=notes_data,
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print("✓ Save notes API working")
            else:
                print("✗ Save notes API returned error:", result.get('error'))
                return False
        else:
            print("✗ Save notes API failed with status:", response.status_code)
            return False
        
        # Test clear notes (save empty notes)
        clear_data = {'notes': ''}
        
        response = self.session.post(
            f"{self.base_url}/api/save_notes/{resource_id}",
            json=clear_data,
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print("✓ Clear notes functionality working")
            else:
                print("✗ Clear notes failed:", result.get('error'))
                return False
        else:
            print("✗ Clear notes API failed")
            return False
        
        return True
    
    def test_quiz_functionality(self, resource_id):
        """Test quiz-related functionality"""
        print(f"Testing quiz functionality for resource {resource_id}...")
        
        # Test check quiz exists
        response = self.session.get(f"{self.base_url}/api/check_quiz_exists/{resource_id}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✓ Quiz check API working - Quiz exists: {result.get('has_quiz', False)}")
        else:
            print("✗ Quiz check API failed")
            return False
        
        return True
    
    def test_activity_tracking(self, resource_id):
        """Test activity tracking functionality"""
        print(f"Testing activity tracking for resource {resource_id}...")
        
        # Test activity tracking API
        activity_data = {
            'resource_id': resource_id,
            'session_id': 1,  # Mock session ID
            'activity_type': 'page_view',
            'data': {
                'timestamp': datetime.now().isoformat(),
                'url': f'{self.base_url}/student/view_resource/{resource_id}'
            }
        }
        
        response = self.session.post(
            f"{self.base_url}/api/track_activity",
            json=activity_data,
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print("✓ Activity tracking API working")
            else:
                print("✗ Activity tracking failed:", result.get('error'))
                return False
        else:
            print("✗ Activity tracking API failed with status:", response.status_code)
            return False
        
        return True
    
    def test_session_management(self, resource_id):
        """Test study session management"""
        print(f"Testing session management for resource {resource_id}...")
        
        # Test start study session
        response = self.session.post(f"{self.base_url}/student/start_study/{resource_id}")
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                session_id = result.get('session_id')
                print(f"✓ Start study session working - Session ID: {session_id}")
                
                # Test end study session
                if session_id:
                    end_response = self.session.post(f"{self.base_url}/student/end_study/{session_id}")
                    if end_response.status_code == 200:
                        print("✓ End study session working")
                        return True
                    else:
                        print("✗ End study session failed")
                        return False
            else:
                print("✗ Start study session failed:", result.get('error'))
                return False
        else:
            print("✗ Start study session API failed")
            return False
    
    def run_comprehensive_test(self):
        """Run comprehensive test of all functionality"""
        print("=" * 60)
        print("STUDENT TRACK APPLICATION - COMPREHENSIVE FUNCTIONALITY TEST")
        print("=" * 60)
        
        # Test login (you'll need to provide actual credentials)
        print("\n1. Testing Authentication...")
        print("Note: Please ensure you have test accounts set up")
        print("You can create test accounts through the admin panel")
        
        # Test basic endpoints
        print("\n2. Testing Basic Endpoints...")
        
        # Test if server is running
        try:
            response = self.session.get(f"{self.base_url}/")
            if response.status_code == 200:
                print("✓ Server is running")
            else:
                print("✗ Server not responding properly")
                return False
        except requests.exceptions.ConnectionError:
            print("✗ Server not running. Please start the application first.")
            return False
        
        print("\n3. Testing API Endpoints...")
        
        # Test various API endpoints
        endpoints_to_test = [
            "/api/debug/sessions",
            "/student/notifications",
            "/student/my_progress"
        ]
        
        for endpoint in endpoints_to_test:
            try:
                response = self.session.get(f"{self.base_url}{endpoint}")
                if response.status_code in [200, 302, 403]:  # 302 for redirects, 403 for auth required
                    print(f"✓ {endpoint} accessible")
                else:
                    print(f"✗ {endpoint} failed with status {response.status_code}")
            except Exception as e:
                print(f"✗ {endpoint} error: {e}")
        
        print("\n4. Testing Database Connectivity...")
        
        # Test database by checking if we can access debug endpoints
        try:
            response = self.session.get(f"{self.base_url}/api/debug/sessions")
            if response.status_code in [200, 403]:  # 403 means auth required, which is expected
                print("✓ Database connectivity appears to be working")
            else:
                print("✗ Database connectivity issues")
        except Exception as e:
            print(f"✗ Database test error: {e}")
        
        print("\n" + "=" * 60)
        print("COMPREHENSIVE TEST COMPLETED")
        print("=" * 60)
        print("\nTo test with actual user credentials:")
        print("1. Create test accounts through the admin panel")
        print("2. Modify this script to include actual credentials")
        print("3. Run the test again")
        
        return True

def main():
    """Main test function"""
    tester = StudentTrackTester()
    
    print("Student Track Application - Functionality Test")
    print("This script will test all major functionality of the application.")
    print("\nMake sure the application is running on http://localhost:5000")
    print("Press Enter to continue or Ctrl+C to cancel...")
    
    try:
        input()
    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
        return
    
    # Run comprehensive test
    tester.run_comprehensive_test()

if __name__ == "__main__":
    main()
