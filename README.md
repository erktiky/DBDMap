# DBDMap
## Info
**DBDMap** is a simple tool for **Dead By Daylight** that automatically displays a minimap of the current map your playing on in the corner of the game.
It uses Pytesseract OCR to scan for text and detect the map name once the loading ends.
## Usage
### Installation
1. Download the **.zip** from the [releases](https://github.com/erktiky/dbdmap/releases/tag/release) page.
2. Unzip the files into a new folder.
### Updating (if only the executable was updated)
1. Download the **.exe** from the [releases](https://github.com/erktiky/dbdmap/releases/tag/release) page.
2. Replace the old **DBDMap.exe** with the **new one**.
### Configuration
1. Open the config.ini file in Notepad (or any other text editor).
2. Choose or find a new SCREENSHOT_REGION using the information provided in config.ini.
3. Adjust any other settings to your liking, such as the minimap size and position, whether the minimap should auto-update or not, or the keybind for updating if manual updating is chosen.
## Modes (Switch with F10)
### Game Mode (Normal Mode)
Use for normal gameplay. Doesn't show any additional info, everything is displayed in the console.
### Preview Mode (Debug Mode)
Use when configurating the SCREENSHOT_REGION. Heavily recommend to disable AUTO_UPDATE before using.
After taking a screenshot, it displays the pre-processed and post-processed versions so you can see if it's positioned correctly and adjust the area of the screenshot.
When the images are focused, press any mouse button to close them and continue using the tool.
## Credits
All the images of the maps are taken from [Hens's Website](https://hens333.com/callouts).
## Example
<img alt="image" src="https://github.com/user-attachments/assets/fe320b8c-b379-4d89-b51a-303655e62d52"/>
