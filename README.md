Autonomous SAR Drone System



Team: 

&#x20;- Luis Gomez - lgomezde@stedwards.edu

&#x20;- Jackie Perales - jperal13@stedwards.edu



Project Description:

&#x20;- An autonomous search \& rescue drone system combining simulated flight, computer vision, and a local operator interface. The system is intended to autonomously search an outdoor area for a person, track search coverage, and flag possible detections for an operator.



Problem Description:

&#x20;- [Problem Description as PDF](https://drive.google.com/file/d/1VSCbiGCWQnIJgjpn5blqHSP8VSKQDBaX/view?usp=drive_link)



Branching Model:

&#x20;- main will always contain the stable deployable version of the project and will be protected. For larger changes or features, we’ll work on separate feature branches and merge them into main through pull requests reviewed by the other team member. Bug fixes will use fix branches. Smaller changes like README edits will also be made on a separate branch and merged into main through a pull request.


## Current Implementation (Gate 3 - November 2026)

The project is a simulation-first autonomous search-and-rescue drone system built with Webots, ArduPilot SITL, MAVLink, Python/FastAPI, and React/TypeScript.

### Implemented Features

- Operator-defined search areas with polygon validation.
- Automatic generation and preview of coverage routes.
- Autonomous mission execution, including takeoff, waypoint navigation, return-to-home, and landing.
- Mission pause, resume, stop, and interrupted-search recovery.
- Live drone telemetry, mission status, and operator controls.
- Live camera feed and YOLO-based person detection with BoT-SORT tracking.
- Reviewable detection candidates with annotated images, approximate map locations, and confirm/dismiss controls.
- Camera-outage safety handling during active missions.

The system has been validated through complete simulated search missions, including person detection, operator confirmation, and autonomous return-to-home.

### Remaining Work

Our final development priorities include:

- WASD-style manual operator controls.
- Terrain-aware altitude control over uneven ground.
- Simplified camera-based search coverage visualization and statistics.
- Offline map availability.
- Additional integration testing, reliability improvements, and interface polish.

Database persistence, historical mission replay, and advanced dynamic search-route optimization have been removed from the planned semester scope.

### Automated Testing

Backend: Python pytest.

Frontend: Vitest.

Gate 3 verification:
- Backend: 188 tests passed.
- Frontend: 231 tests passed.
- Total: 419 automated tests passed across separately executed suites.

Backend test command (from backend/):

    python -m pytest tests -q

Frontend test command (from frontend/):

    npm run test -- --run

End-to-end behavior is also tested manually using Webots and ArduPilot SITL.

### Project Limitations

This is a simulated research prototype, not a production-ready emergency-response system. Current autonomous missions use home-relative altitude rather than continuous terrain following. Person locations are approximate, and detection performance is not guaranteed across all environments.

