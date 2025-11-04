#!/usr/bin/env python3
"""
Initialize the student notes database table
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db, StudentNotes
from sqlalchemy import text

def init_notes_database():
    """Initialize the student notes database table"""
    print("Initializing Student Notes Database...")
    
    with app.app_context():
        try:
            # Create all tables
            db.create_all()
            print("✓ Database tables created/verified")
            
            # Check if student_notes table exists
            result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='student_notes'"))
            table_exists = result.fetchone()
            
            if table_exists:
                print("✓ student_notes table exists")
                
                # Check table structure
                result = db.session.execute(text("PRAGMA table_info(student_notes)"))
                columns = [row[1] for row in result.fetchall()]
                print(f"✓ Table columns: {columns}")
                
                # Check for required columns and add if missing
                required_columns = {
                    'id': 'INTEGER PRIMARY KEY',
                    'student_id': 'INTEGER NOT NULL',
                    'resource_id': 'INTEGER NOT NULL', 
                    'notes_content': 'TEXT NOT NULL',
                    'created_at': 'DATETIME',
                    'updated_at': 'DATETIME',
                    'word_count': 'INTEGER DEFAULT 0',
                    'character_count': 'INTEGER DEFAULT 0',
                    'engagement_score': 'REAL DEFAULT 0.0',
                    'teacher_grade': 'REAL NULL',
                    'teacher_feedback': 'TEXT NULL',
                    'graded_at': 'DATETIME NULL',
                    'graded_by': 'INTEGER NULL'
                }
                
                existing_columns = set(columns)
                for col_name, col_def in required_columns.items():
                    if col_name not in existing_columns:
                        print(f"Adding missing column: {col_name}")
                        try:
                            db.session.execute(text(f"ALTER TABLE student_notes ADD COLUMN {col_name} {col_def}"))
                            db.session.commit()
                            print(f"✓ Added column: {col_name}")
                        except Exception as e:
                            print(f"✗ Failed to add column {col_name}: {e}")
                
                # Create unique constraint if it doesn't exist
                try:
                    db.session.execute(text("""
                        CREATE UNIQUE INDEX IF NOT EXISTS unique_student_resource_notes 
                        ON student_notes (student_id, resource_id)
                    """))
                    db.session.commit()
                    print("✓ Unique constraint created/verified")
                except Exception as e:
                    print(f"Note: Unique constraint may already exist: {e}")
                
                # Test insert/delete
                print("Testing database operations...")
                test_notes = StudentNotes(
                    student_id=999,  # Use a test ID
                    resource_id=999,  # Use a test ID
                    notes_content="Test notes for database verification",
                    word_count=6,
                    character_count=35,
                    engagement_score=25.0
                )
                
                db.session.add(test_notes)
                db.session.commit()
                print("✓ Test insert successful")
                
                # Clean up test data
                db.session.delete(test_notes)
                db.session.commit()
                print("✓ Test cleanup successful")
                
                print("\n🎉 Student Notes Database is ready!")
                return True
                
            else:
                print("✗ student_notes table does not exist")
                return False
                
        except Exception as e:
            print(f"✗ Database initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = init_notes_database()
    sys.exit(0 if success else 1)
