#!/usr/bin/env python3
"""
Debug script to check database issues with essay answers and student notes
"""

import sqlite3
import os
from datetime import datetime

def check_database():
    db_path = 'instance/students.db'
    if not os.path.exists(db_path):
        print(f"Database file not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print("Existing tables:", tables)
    
    # Check StudentAnswer table structure
    if 'student_answer' in tables:
        cursor.execute("PRAGMA table_info(student_answer)")
        columns = cursor.fetchall()
        print("\nStudentAnswer columns:")
        for col in columns:
            print(f"  {col[1]} ({col[2]}) - {'NOT NULL' if col[3] else 'NULL'}")
        
        # Check for essay answers
        cursor.execute("SELECT COUNT(*) FROM student_answer sa JOIN question q ON sa.question_id = q.id WHERE q.question_type = 'essay'")
        essay_count = cursor.fetchone()[0]
        print(f"\nEssay answers in database: {essay_count}")
        
        if essay_count > 0:
            cursor.execute("""
                SELECT sa.id, sa.answer, sa.student_id, q.question_text, s.name 
                FROM student_answer sa 
                JOIN question q ON sa.question_id = q.id 
                JOIN student s ON sa.student_id = s.id 
                WHERE q.question_type = 'essay' 
                LIMIT 5
            """)
            essays = cursor.fetchall()
            print("\nSample essay answers:")
            for essay in essays:
                print(f"  ID: {essay[0]}, Student: {essay[4]}, Answer: {essay[1][:50]}...")
    else:
        print("\nStudentAnswer table not found!")
    
    # Check StudentNotes table structure
    if 'student_notes' in tables:
        cursor.execute("PRAGMA table_info(student_notes)")
        columns = cursor.fetchall()
        print("\nStudentNotes columns:")
        for col in columns:
            print(f"  {col[1]} ({col[2]}) - {'NOT NULL' if col[3] else 'NULL'}")
        
        # Check for student notes
        cursor.execute("SELECT COUNT(*) FROM student_notes")
        notes_count = cursor.fetchone()[0]
        print(f"\nStudent notes in database: {notes_count}")
        
        if notes_count > 0:
            cursor.execute("""
                SELECT sn.id, sn.notes_content, sn.student_id, s.name, r.title 
                FROM student_notes sn 
                JOIN student s ON sn.student_id = s.id 
                JOIN resource r ON sn.resource_id = r.id 
                LIMIT 5
            """)
            notes = cursor.fetchall()
            print("\nSample student notes:")
            for note in notes:
                print(f"  ID: {note[0]}, Student: {note[3]}, Resource: {note[4]}, Notes: {note[1][:50]}...")
    else:
        print("\nStudentNotes table not found!")
    
    conn.close()

if __name__ == "__main__":
    check_database()
