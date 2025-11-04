#!/usr/bin/env python3
"""
Deployment Preparation Script
Checks if your project is ready for hosting deployment
"""

import os
import sys
from pathlib import Path

def check_file_exists(filepath, description):
    """Check if a required file exists"""
    if os.path.exists(filepath):
        print(f"✅ {description}: {filepath}")
        return True
    else:
        print(f"❌ MISSING: {description}: {filepath}")
        return False

def check_directory_exists(dirpath, description):
    """Check if a required directory exists"""
    if os.path.isdir(dirpath):
        file_count = len(list(Path(dirpath).rglob('*')))
        print(f"✅ {description}: {dirpath} ({file_count} files)")
        return True
    else:
        print(f"❌ MISSING: {description}: {dirpath}")
        return False

def check_sensitive_files():
    """Check that sensitive files are not being committed"""
    sensitive_files = [
        'env_file.txt',
        '.env',
        'instance/students.db',
        'venv/',
    ]
    
    print("\n🔒 Checking for sensitive files (should NOT be in git):")
    all_safe = True
    
    for filepath in sensitive_files:
        if os.path.exists(filepath):
            # Check if it's in .gitignore
            gitignore_path = '.gitignore'
            if os.path.exists(gitignore_path):
                with open(gitignore_path, 'r') as f:
                    gitignore_content = f.read()
                    if filepath in gitignore_content or os.path.basename(filepath) in gitignore_content:
                        print(f"⚠️  {filepath} exists but should be in .gitignore - VERIFY it's not committed!")
                    else:
                        print(f"❌ WARNING: {filepath} exists and may not be in .gitignore!")
                        all_safe = False
        else:
            print(f"✅ {filepath} doesn't exist (good)")
    
    return all_safe

def check_requirements():
    """Check requirements.txt exists and has content"""
    if os.path.exists('requirements.txt'):
        with open('requirements.txt', 'r') as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
            print(f"✅ requirements.txt found with {len(lines)} packages")
            return True
    else:
        print("❌ requirements.txt not found!")
        return False

def main():
    print("=" * 60)
    print("🚀 DEPLOYMENT PREPARATION CHECK")
    print("=" * 60)
    
    issues = []
    
    # Check required files
    print("\n📁 Checking required files:")
    if not check_file_exists('app.py', 'Main application'):
        issues.append("app.py is missing")
    
    if not check_file_exists('requirements.txt', 'Dependencies file'):
        issues.append("requirements.txt is missing")
    
    if not check_file_exists('Procfile', 'Process configuration'):
        issues.append("Procfile is missing")
    
    if not check_file_exists('runtime.txt', 'Python version'):
        issues.append("runtime.txt is missing")
    
    # Check required directories
    print("\n📂 Checking required directories:")
    if not check_directory_exists('templates', 'Templates directory'):
        issues.append("templates/ directory is missing")
    
    if not check_directory_exists('static', 'Static files directory'):
        issues.append("static/ directory is missing")
    
    # Check sensitive files
    if not check_sensitive_files():
        issues.append("Sensitive files may be exposed")
    
    # Check .gitignore
    print("\n🔍 Checking .gitignore:")
    if check_file_exists('.gitignore', 'Git ignore file'):
        print("✅ .gitignore exists")
    else:
        print("⚠️  .gitignore not found - create one to protect sensitive files!")
        issues.append(".gitignore missing")
    
    # Summary
    print("\n" + "=" * 60)
    if issues:
        print("❌ ISSUES FOUND:")
        for issue in issues:
            print(f"   - {issue}")
        print("\n⚠️  Please fix these issues before deploying!")
        return 1
    else:
        print("✅ ALL CHECKS PASSED!")
        print("\n📋 Next Steps:")
        print("   1. Generate SECRET_KEY: python -c \"import secrets; print(secrets.token_hex(32))\"")
        print("   2. Push to GitHub (if not already done)")
        print("   3. Follow HOSTING_COMPLETE_GUIDE.md for deployment")
        print("\n🎉 Your project is ready for deployment!")
        return 0

if __name__ == '__main__':
    sys.exit(main())

