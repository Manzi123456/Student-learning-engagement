from app import app

if __name__ == '__main__':
    # Enable debug mode for detailed error pages
    app.debug = True
    # Enable auto-reload for development
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    # Show detailed error messages
    app.config['PROPAGATE_EXCEPTIONS'] = True
    # Run the app
    app.run(host='0.0.0.0', port=5000, debug=True)
