#!/usr/bin/env python3
"""
Test database functionality
"""

import os
import sys
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the app
from app import app, db, StudentAnswer, StudentNotes, Question, Student, Resource

def test_database():
    """Test database functionality"""
    with app.app_context():
        try:
            # Test database connection
            db.session.execute(db.text("SELECT 1"))
            print("✓ Database connection successful")
            
            # Test creating tables
            db.create_all()
            print("✓ Tables created/verified")
            
            # Test querying tables
            questions = Question.query.all()
            print(f"✓ Questions in database: {len(questions)}")
            
            students = Student.query.all()
            print(f"✓ Students in database: {len(students)}")
            
            resources = Resource.query.all()
            print(f"✓ Resources in database: {len(resources)}")
            
            # Test StudentAnswer table
            answers = StudentAnswer.query.all()
            print(f"✓ Student answers in database: {len(answers)}")
            
            # Test StudentNotes table
            notes = StudentNotes.query.all()
            print(f"✓ Student notes in database: {len(notes)}")
            
            # Test essay questions
            essay_questions = Question.query.filter_by(question_type='essay').all()
            print(f"✓ Essay questions: {len(essay_questions)}")
            
            # Test essay answers
            essay_answers = db.session.query(StudentAnswer).join(Question).filter(Question.question_type == 'essay').all()
            print(f"✓ Essay answers: {len(essay_answers)}")
            
            if essay_answers:
                print("Sample essay answers:")
                for answer in essay_answers[:3]:
                    student = Student.query.get(answer.student_id)
                    question = Question.query.get(answer.question_id)
                    print(f"  - Student: {student.name if student else 'Unknown'}, Question: {question.question_text[:50] if question else 'Unknown'}...")
            
            print("\n✓ Database test completed successfully")
            return True
            
        except Exception as e:
            print(f"✗ Database test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    test_database()
