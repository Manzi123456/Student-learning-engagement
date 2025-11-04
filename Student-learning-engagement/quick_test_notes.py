#!/usr/bin/env python3
"""
Quick test for student notes functionality
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db, StudentNotes
from sqlalchemy import text

def quick_test():
    """Quick test of the notes functionality"""
    print("Quick Test - Student Notes Functionality")
    print("=" * 50)
    
    with app.app_context():
        try:
            # Test database connection
            db.create_all()
            print("✓ Database connection successful")
            
            # Check if table exists
            result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='student_notes'"))
            if result.fetchone():
                print("✓ student_notes table exists")
                
                # Check table structure
                result = db.session.execute(text("PRAGMA table_info(student_notes)"))
                columns = [row[1] for row in result.fetchall()]
                print(f"✓ Table has {len(columns)} columns: {columns}")
                
                # Test insert
                test_notes = StudentNotes(
                    student_id=1,
                    resource_id=2,
                    notes_content="Test notes from quick test",
                    word_count=5,
                    character_count=25,
                    engagement_score=12.5
                )
                db.session.add(test_notes)
                db.session.commit()
                print("✓ Test insert successful")
                
                # Test query
                notes = StudentNotes.query.filter_by(student_id=1, resource_id=2).first()
                if notes:
                    print(f"✓ Test query successful: {notes.notes_content}")
                else:
                    print("✗ Test query failed")
                
                # Clean up
                db.session.delete(test_notes)
                db.session.commit()
                print("✓ Test cleanup successful")
                
                print("\n🎉 Database functionality is working correctly!")
                return True
            else:
                print("✗ student_notes table does not exist")
                return False
                
        except Exception as e:
            print(f"✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = quick_test()
    sys.exit(0 if success else 1)
