#!/usr/bin/env python3
"""
Simple script to verify the notes table exists and is working
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db, StudentNotes
    from sqlalchemy import text
    
    with app.app_context():
        print("Checking database...")
        
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
            
            # Test insert
            try:
                # Check if we have any data
                count = db.session.execute(text("SELECT COUNT(*) FROM student_notes")).fetchone()[0]
                print(f"✓ Current notes count: {count}")
                
            except Exception as e:
                print(f"✗ Error checking data: {e}")
                
        else:
            print("✗ student_notes table does not exist")
            
        print("Database check completed")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
