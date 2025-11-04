#!/usr/bin/env python3
"""
Simple test to check database
"""

import sqlite3
import os

def test_sqlite():
    """Test SQLite database directly"""
    db_path = 'instance/students.db'
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"Tables: {tables}")
        
        # Check student_answer table
        if 'student_answer' in tables:
            cursor.execute("SELECT COUNT(*) FROM student_answer")
            count = cursor.fetchone()[0]
            print(f"Student answers: {count}")
            
            if count > 0:
                cursor.execute("SELECT id, answer, student_id, question_id FROM student_answer LIMIT 3")
                rows = cursor.fetchall()
                print("Sample answers:")
                for row in rows:
                    print(f"  ID: {row[0]}, Answer: {row[1][:50]}..., Student: {row[2]}, Question: {row[3]}")
        else:
            print("student_answer table not found")
        
        # Check student_notes table
        if 'student_notes' in tables:
            cursor.execute("SELECT COUNT(*) FROM student_notes")
            count = cursor.fetchone()[0]
            print(f"Student notes: {count}")
            
            if count > 0:
                cursor.execute("SELECT id, notes_content, student_id, resource_id FROM student_notes LIMIT 3")
                rows = cursor.fetchall()
                print("Sample notes:")
                for row in rows:
                    print(f"  ID: {row[0]}, Notes: {row[1][:50]}..., Student: {row[2]}, Resource: {row[3]}")
        else:
            print("student_notes table not found")
        
        # Check question table
        if 'question' in tables:
            cursor.execute("SELECT COUNT(*) FROM question WHERE question_type = 'essay'")
            count = cursor.fetchone()[0]
            print(f"Essay questions: {count}")
        else:
            print("question table not found")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    test_sqlite()
