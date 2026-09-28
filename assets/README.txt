Place the Windows static binaries here for local builds:

  ffmpeg.exe
  ffplay.exe

Recommended source (essentials build):
  https://www.gyan.dev/ffmpeg/builds/

The GitHub Actions workflow downloads these automatically —
you only need them for local-build.bat / manual PyInstaller runs.

Do not commit the .exe files (they are listed in .gitignore).
