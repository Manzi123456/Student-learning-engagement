#!/usr/bin/env python3
"""
Initialize database and check for issues
"""

import os
import sys
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the app
from app import app, db

def init_database():
    """Initialize database and check for issues"""
    with app.app_context():
        try:
            # Create all tables
            db.create_all()
            print("Database tables created successfully")
            
            # Check if tables exist
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            print(f"Tables in database: {tables}")
            
            # Check StudentAnswer table
            if 'student_answer' in tables:
                columns = inspector.get_columns('student_answer')
                print(f"StudentAnswer columns: {[c['name'] for c in columns]}")
            
            # Check StudentNotes table
            if 'student_notes' in tables:
                columns = inspector.get_columns('student_notes')
                print(f"StudentNotes columns: {[c['name'] for c in columns]}")
            
            print("Database initialization completed successfully")
            
        except Exception as e:
            print(f"Error initializing database: {e}")
            return False
    
    return True

if __name__ == "__main__":
    init_database()
