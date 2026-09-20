"""Open an existing local Workbench or start a new one."""
import json
import urllib.request
import webbrowser

URL = 'http://127.0.0.1:8765'
if __name__ == '__main__':
    try:
        with urllib.request.urlopen(URL + '/api/health', timeout=2) as response:
            running = json.load(response).get('app') == 'emerald-workbench'
    except Exception:
        running = False
    if running:
        webbrowser.open(URL + '/worldmap')
    else:
        import sys
        from server import main
        sys.argv = [sys.argv[0], '--open']
        main()
