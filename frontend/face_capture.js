import {
    FaceLandmarker,
    FilesetResolver
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";

const video = document.getElementById("video");

const startButton = document.getElementById("startCamera");
const stopButton = document.getElementById("stopCamera");
const restartButton = document.getElementById("restartCapture");

const instruction = document.getElementById("instruction");
const faceStatus = document.getElementById("faceStatus");
const lightStatus = document.getElementById("lightStatus");
const blurStatus = document.getElementById("blurStatus");
const cameraMessage = document.getElementById("cameraMessage");
const poseInstruction = instruction;
const progressFill = document.getElementById("progressFill");
const progressText = document.getElementById("progressText");
const progressTrack = document.querySelector('[role="progressbar"]');
const submitDataset = document.getElementById("submitDataset");
const voiceGuidance = document.getElementById("voiceGuidance");
const studentSession = document.getElementById("studentSession");
const registeredUSN = (localStorage.getItem("registeredUSN") || "").trim().toUpperCase();

const poseSteps = ["RIGHT", "LEFT", "UP", "DOWN", "FRONT", "BLINK"];
const imagesPerPose = 3;
const capturedImages = [];
const captureCanvas = document.createElement("canvas");
const captureContext = captureCanvas.getContext("2d");

let stream = null;
let faceLandmarker = null;
let running = false;
let neutralPitch = null;
let neutralPitchSamples = [];
let currentStep = 0;
let stableFrames = 0;
let eyesWereClosed = false;
let blinkDetected = false;
let isCapturing = false;
let isUploading = false;
let captureRevision = 0;
let faceMissingSince = null;
let stepStartedAt = performance.now();
let stepHintShown = false;
const faceLossResetDelayMs = 1000;
const stepHintDelayMs = 6000;

if (registeredUSN) {
    studentSession.textContent = `Registered student: ${registeredUSN}`;
} else {
    studentSession.textContent = "Register a student first to begin face capture.";
    startButton.disabled = true;
}

function resetCaptureProgress() {
    captureRevision++;
    currentStep = 0;
    stableFrames = 0;
    eyesWereClosed = false;
    blinkDetected = false;
    capturedImages.length = 0;
    isCapturing = false;
    neutralPitch = null;
    neutralPitchSamples = [];
    stepStartedAt = performance.now();
    stepHintShown = false;
    instruction.classList.remove("is-hint");
    progressFill.style.width = "0%";
    progressText.textContent = "0% Completed";
    progressTrack.setAttribute("aria-valuenow", "0");
    submitDataset.disabled = true;
    updatePoseIndicators();
}

function updatePoseIndicators(activePose = null, detected = false) {
    const stepIndexes = Object.fromEntries(poseSteps.map((pose, index) => [pose, index]));

    document.querySelectorAll(".pose-status").forEach((indicator) => {
        const pose = indicator.dataset.pose;
        const poseIndex = stepIndexes[pose];
        indicator.classList.remove("is-active", "is-detected", "is-complete");

        if (poseIndex < currentStep) {
            indicator.classList.add("is-complete");
            indicator.textContent = `${pose} ✓`;
        } else if (pose === activePose && detected) {
            indicator.classList.add("is-detected");
            indicator.textContent = `${pose} DETECTED`;
        } else if (pose === activePose) {
            indicator.classList.add("is-active");
            indicator.textContent = `${pose} — MOVE NOW`;
        } else {
            indicator.textContent = pose;
        }
    });
}

function speakCountdown(number) {
    if (!voiceGuidance.checked || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const words = { 3: "three", 2: "two", 1: "one" };
    window.speechSynthesis.speak(
        new SpeechSynthesisUtterance(`Hold still. ${words[number]}`)
    );
}

async function capturePoseImages(position) {
    if (isCapturing) return;
    isCapturing = true;
    const revision = captureRevision;
    const poseFiles = [];

    try {
        poseInstruction.textContent = `Capturing ${position} images...`;
        captureCanvas.width = video.videoWidth;
        captureCanvas.height = video.videoHeight;

        if (!captureCanvas.width || !captureCanvas.height) {
            throw new Error("Camera image is not ready. Please try the pose again.");
        }

        for (let count = 3; count >= 1; count--) {
            const countdownResult = faceLandmarker.detectForVideo(video, performance.now());
            const countdownFaces = countdownResult.faceLandmarks;
            if (countdownFaces.length !== 1 || detectHeadPosition(countdownFaces[0]) !== position) {
                throw new Error(`Hold the ${position} pose. The countdown was cancelled.`);
            }
            if (!checkLighting() || !checkBlur()) {
                throw new Error("Lighting or image clarity changed. Fix it and try this pose again.");
            }

            poseInstruction.textContent = `Hold still. Capturing ${position} in ${count}...`;
            speakCountdown(count);
            await new Promise((resolve) => setTimeout(resolve, 1000));
        }

        let capturedCount = 0;
        let attempts = 0;

        while (capturedCount < imagesPerPose && attempts < 30) {
            attempts++;
            if (revision !== captureRevision) throw new Error("Capture was reset. Start the sequence again.");
            captureContext.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height);

            // Run quality checks against the exact canvas frame that would be saved.
            const lightingOK = checkLighting(captureCanvas);
            const clarityOK = checkBlur(captureCanvas);
            const result = faceLandmarker.detectForVideo(captureCanvas, performance.now());
            const faces = result.faceLandmarks;
            const oneFace = faces && faces.length === 1;
            const poseOK = oneFace && detectHeadPosition(faces[0]) === position;

            if (!lightingOK || !clarityOK || !oneFace || !poseOK) {
                updatePoseIndicators(position, false);
                poseInstruction.textContent = !lightingOK || !clarityOK
                    ? "Improve lighting or image clarity; holding for another frame..."
                    : !oneFace
                        ? "Keep exactly one face in frame; holding for another frame..."
                        : `Hold the ${position} pose; holding for another frame...`;
                await new Promise((resolve) => setTimeout(resolve, 500));
                continue;
            }

            const blob = await new Promise((resolve) => captureCanvas.toBlob(resolve, "image/jpeg", 0.95));
            if (!blob) {
                await new Promise((resolve) => setTimeout(resolve, 500));
                continue;
            }

            poseFiles.push(new File(
                [blob],
                `${position.toLowerCase()}_${capturedCount + 1}.jpg`,
                { type: "image/jpeg" }
            ));
            capturedCount++;
            updatePoseIndicators(position, true);
            poseInstruction.textContent = `${position}: ${capturedCount}/${imagesPerPose} quality images captured`;
            if (capturedCount < imagesPerPose) await new Promise((resolve) => setTimeout(resolve, 500));
        }

        if (capturedCount !== imagesPerPose) {
            throw new Error(`Could not capture all ${imagesPerPose} quality images for ${position}. Please try again.`);
        }

        if (revision !== captureRevision) throw new Error("Capture was reset. Start the sequence again.");
        capturedImages.push(...poseFiles);
    } finally {
        isCapturing = false;
    }
}

async function submitStudentDataset() {
    if (!registeredUSN) {
        poseInstruction.textContent = "Register a student before uploading a dataset.";
        return;
    }
    if (capturedImages.length !== 15 || currentStep !== poseSteps.length || !blinkDetected) {
        poseInstruction.textContent = "Complete all six instructions and capture all 15 images first.";
        return;
    }

    const formData = new FormData();
    capturedImages.forEach((file) => formData.append("files", file));
    isUploading = true;
    submitDataset.disabled = true;
    restartButton.disabled = true;
    poseInstruction.textContent = "Uploading your face dataset...";

    try {
        const response = await fetch(
            `http://127.0.0.1:8000/upload-dataset/${encodeURIComponent(registeredUSN)}`,
            { method: "POST", body: formData }
        );
        const result = await response.json();
        if (!response.ok) {
            const detail = result.detail;
            const message = typeof detail === "string"
                ? detail
                : detail?.message
                    ? `${detail.message} Matched student: ${detail.matched_student} (similarity ${Number(detail.similarity).toFixed(3)}).`
                    : "Dataset upload failed.";
            throw new Error(message);
        }

        stopCamera(false);
        poseInstruction.textContent = "Dataset uploaded successfully!";
        cameraMessage.textContent = `${result.image_count} images saved for ${result.usn}.`;
        submitDataset.disabled = true;
    } catch (error) {
        poseInstruction.textContent = error.message || "Dataset upload failed. Please try again.";
        submitDataset.disabled = false;
    } finally {
        isUploading = false;
        restartButton.disabled = false;
    }
}

function speakInstruction(step) {
    if (!voiceGuidance.checked || !("speechSynthesis" in window)) return;

    const phrases = {
        RIGHT: "Move your head right",
        LEFT: "Move your head left",
        UP: "Look up",
        DOWN: "Look down",
        FRONT: "Look straight at the camera",
        BLINK: "Blink your eyes once"
    };
    const phrase = phrases[step];
    if (!phrase) return;

    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(phrase));
}

function speakHint(step) {
    if (!voiceGuidance.checked || !("speechSynthesis" in window)) return;

    const phrases = {
        RIGHT: "Look straight first, then move your head right",
        LEFT: "Look straight first, then move your head left",
        UP: "Look straight first, then look up",
        DOWN: "Look straight first, then look down",
        FRONT: "Look straight at the camera and hold still",
        BLINK: "Look straight first, then blink your eyes once"
    };

    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(phrases[step]));
}

async function loadFaceModel() {

    instruction.textContent = "Loading face detection model...";

    const vision = await FilesetResolver.forVisionTasks(
        "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm"
    );

    faceLandmarker = await FaceLandmarker.createFromOptions(
        vision,
        {
            baseOptions: {
                modelAssetPath:
                    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
                delegate: "CPU"
            },

            runningMode: "VIDEO",
            numFaces: 2
        }
    );
}

async function startCamera() {

    try {

        if (!faceLandmarker) {
            await loadFaceModel();
        }

        stream = await navigator.mediaDevices.getUserMedia({
            video: {
                facingMode: "user",
                width: { ideal: 1280 },
                height: { ideal: 720 }
            },
            audio: false
        });

        video.srcObject = stream;

        await video.play();

        running = true;
        stepStartedAt = performance.now();
        stepHintShown = false;
        neutralPitch = null;
        neutralPitchSamples = [];

        startButton.disabled = true;
        stopButton.disabled = false;

        cameraMessage.textContent = "Camera is active";

        instruction.textContent = "Look straight at the camera for head-pose calibration";
        speakInstruction("FRONT");

        detectFace();

    } catch (error) {

        instruction.textContent = "Camera could not start";

        cameraMessage.textContent = error.message;

        console.error(error);
    }
}

function checkLighting(source = video) {

    const canvas = document.createElement("canvas");

    canvas.width = 160;
    canvas.height = 120;

    const context = canvas.getContext("2d");

    context.drawImage(source, 0, 0, 160, 120);

    const image = context.getImageData(
        0, 0, 160, 120
    );

    let totalBrightness = 0;

    for (let i = 0; i < image.data.length; i += 4) {

        const brightness =
            (image.data[i] +
             image.data[i + 1] +
             image.data[i + 2]) / 3;

        totalBrightness += brightness;
    }

    const average =
        totalBrightness / (image.data.length / 4);

    if (average < 65) {

        lightStatus.textContent =
            "Lighting: Too dark - Move to a brighter place";

        lightStatus.style.color = "red";

        return false;

    } else {

        lightStatus.textContent =
            "Lighting: Sufficient";

        lightStatus.style.color = "green";

        return true;
    }
}

function checkBlur(source = video) {

    const canvas = document.createElement("canvas");

    canvas.width = 160;
    canvas.height = 120;

    const context = canvas.getContext("2d");

    context.drawImage(source, 0, 0, 160, 120);

    const pixels = context.getImageData(
        0, 0, 160, 120
    ).data;

    const gray = new Float32Array(160 * 120);

    for (let i = 0; i < gray.length; i++) {

        const j = i * 4;

        gray[i] =
            0.299 * pixels[j] +
            0.587 * pixels[j + 1] +
            0.114 * pixels[j + 2];
    }

    let sum = 0;
    let sumSquares = 0;
    let count = 0;

    for (let y = 1; y < 119; y++) {

        for (let x = 1; x < 159; x++) {

            const i = y * 160 + x;

            const laplacian =
                gray[i - 160] +
                gray[i + 160] +
                gray[i - 1] +
                gray[i + 1] -
                4 * gray[i];

            sum += laplacian;
            sumSquares += laplacian * laplacian;

            count++;
        }
    }

    const mean = sum / count;

    const variance =
        sumSquares / count - mean * mean;

    // Initial heuristic; calibrate with real camera samples.
    if (variance < 40) {

        blurStatus.textContent =
            "Image clarity: Possibly blurred";

        blurStatus.style.color = "red";

        return false;

    } else {

        blurStatus.textContent =
            "Image clarity: Looks clear";

        blurStatus.style.color = "green";

        return true;
    }
}

function getHeadPoseMetrics(landmarks) {
    const nose = landmarks[1];
    const leftEye = landmarks[33];
    const rightEye = landmarks[263];
    const forehead = landmarks[10];
    const chin = landmarks[152];

    const eyeCenterX = (leftEye.x + rightEye.x) / 2;
    const eyeDistance = Math.abs(rightEye.x - leftEye.x);
    const eyeCenterY = (leftEye.y + rightEye.y) / 2;
    const faceHeight = Math.abs(chin.y - forehead.y);

    return {
        yaw: eyeDistance > 0 ? (nose.x - eyeCenterX) / eyeDistance : 0,
        pitch: faceHeight > 0 ? (nose.y - eyeCenterY) / faceHeight : 0
    };
}

function calibrateNeutralPitch(landmarks) {
    const { yaw, pitch } = getHeadPoseMetrics(landmarks);

    if (Math.abs(yaw) > 0.12) {
        neutralPitchSamples = [];
        instruction.textContent = "Look straight at the camera to calibrate";
        return false;
    }

    neutralPitchSamples.push(pitch);
    if (neutralPitchSamples.length < 18) {
        instruction.textContent = `Keep looking straight for calibration (${neutralPitchSamples.length}/18)`;
        return false;
    }

    const sorted = [...neutralPitchSamples].sort((a, b) => a - b);
    neutralPitch = sorted[Math.floor(sorted.length / 2)];
    neutralPitchSamples = [];
    instruction.textContent = "Move your head RIGHT";
    speakInstruction("RIGHT");
    return true;
}

function detectHeadPosition(landmarks) {
    const { yaw, pitch } = getHeadPoseMetrics(landmarks);
    const verticalDelta = pitch - (neutralPitch ?? 0.365);
    const poseThreshold = 0.045;

    if (yaw < -0.15) return "LEFT";
    if (yaw > 0.15) return "RIGHT";
    if (verticalDelta < -poseThreshold) return "UP";
    if (verticalDelta > poseThreshold) return "DOWN";
    return "FRONT";
}

function calculateEAR(landmarks, eye) {
    const distance = (a, b) => Math.hypot(
        landmarks[a].x - landmarks[b].x,
        landmarks[a].y - landmarks[b].y
    );

    const vertical1 = distance(eye[1], eye[5]);
    const vertical2 = distance(eye[2], eye[4]);
    const horizontal = distance(eye[0], eye[3]);

    return horizontal === 0 ? 0 : (vertical1 + vertical2) / (2 * horizontal);
}

function detectBlink(landmarks) {
    const leftEAR = calculateEAR(landmarks, [33, 160, 158, 133, 153, 144]);
    const rightEAR = calculateEAR(landmarks, [362, 385, 387, 263, 373, 380]);
    const averageEAR = (leftEAR + rightEAR) / 2;

    if (averageEAR < 0.20) {
        eyesWereClosed = true;
        return false;
    }

    if (eyesWereClosed && averageEAR > 0.23) {
        eyesWereClosed = false;
        blinkDetected = true;
        return true;
    }

    return false;
}

async function updateProgress(position) {
    if (currentStep >= poseSteps.length || isCapturing || isUploading) return;

    const expected = poseSteps[currentStep];
    let completed = false;
    const hint = expected === "BLINK"
        ? "Look straight first, then blink your eyes once"
        : expected === "FRONT"
            ? "Look straight at the camera and hold still"
            : `Look straight first, then move your head ${expected}`;
    const showHint = !stepHintShown && performance.now() - stepStartedAt >= stepHintDelayMs;

    if (expected === "BLINK") {
        poseInstruction.textContent = showHint || stepHintShown ? hint : "Please blink your eyes once";
        if (showHint) {
            stepHintShown = true;
            instruction.classList.add("is-hint");
            speakHint(expected);
        }
        if (detectBlink(position)) completed = true;
        updatePoseIndicators(expected, completed);
    } else if (position === expected) {
        stepHintShown = false;
        instruction.classList.remove("is-hint");
        stableFrames++;
        poseInstruction.textContent = "Good! Hold this position";
        updatePoseIndicators(expected, true);
        if (stableFrames >= 8) completed = true;
    } else {
        stableFrames = 0;
        poseInstruction.textContent = showHint || stepHintShown ? hint : "Move your head " + expected;
        updatePoseIndicators(expected, false);
        if (showHint) {
            stepHintShown = true;
            instruction.classList.add("is-hint");
            speakHint(expected);
        }
    }

    if (completed) {
        if (expected !== "BLINK") {
            try {
                await capturePoseImages(expected);
            } catch (error) {
                stableFrames = 0;
                updatePoseIndicators(expected, false);
                poseInstruction.textContent = error.message || "Capture failed. Please try the pose again.";
                return;
            }
        }

        currentStep++;
        stableFrames = 0;
        stepStartedAt = performance.now();
        stepHintShown = false;
        instruction.classList.remove("is-hint");
        updatePoseIndicators();
        const progress = Math.round((currentStep / poseSteps.length) * 100);
        progressFill.style.width = progress + "%";
        progressText.textContent = progress + "% Completed";
        progressTrack.setAttribute("aria-valuenow", String(progress));

        if (currentStep === poseSteps.length) {
            poseInstruction.textContent = "All instructions completed! Uploading requires 15 captured images.";
            submitDataset.disabled = capturedImages.length !== 15 || !blinkDetected;
        } else {
            const next = poseSteps[currentStep];
            poseInstruction.textContent = next === "BLINK"
                ? "Now blink your eyes once"
                : "Now move your head " + next;
            speakInstruction(next);
        }
    }
}


function detectFace() {

    if (!running) return;

    if (video.readyState >= 2) {

        const result = faceLandmarker.detectForVideo(
            video,
            performance.now()
        );

        const faces = result.faceLandmarks;

        if (faces.length === 0) {

            faceStatus.textContent = "Face: Not detected";
            if (neutralPitch === null) neutralPitchSamples = [];
            if (neutralPitch !== null && currentStep < poseSteps.length) {
                updatePoseIndicators(poseSteps[currentStep], false);
            }

            instruction.textContent =
                "Move your face into the camera frame";

            if (faceMissingSince === null) {
                faceMissingSince = performance.now();
            } else if (performance.now() - faceMissingSince >= faceLossResetDelayMs && currentStep > 0) {
                resetCaptureProgress();
                cameraMessage.textContent = "Face lost. Capture sequence reset; one person must complete all steps.";
                instruction.textContent = "Capture sequence reset. Start again with the same person.";
            }

        } else if (faces.length > 1) {

            faceMissingSince = null;
            neutralPitch = null;
            neutralPitchSamples = [];
            if (currentStep > 0) {
                resetCaptureProgress();
                cameraMessage.textContent = "Multiple faces detected. Capture sequence reset.";
            }

            faceStatus.textContent =
                "Multiple faces detected";
            updatePoseIndicators(currentStep < poseSteps.length ? poseSteps[currentStep] : null, false);

            instruction.textContent =
                "Only one person should be in the frame";

        } else {

            faceMissingSince = null;

            faceStatus.textContent = "Face: Detected";

            const lightingOK = checkLighting();
            const clarityOK = checkBlur();

            if (lightingOK && clarityOK) {

                if (neutralPitch === null) {
                    calibrateNeutralPitch(faces[0]);
                } else {
                    if (poseSteps[currentStep] === "BLINK") {
                        updateProgress(faces[0]);
                    } else {
                        const position = detectHeadPosition(faces[0]);
                        updateProgress(position);
                    }
                }

            } else {

                instruction.textContent =
                    "Improve lighting and image clarity";
                if (neutralPitch !== null && currentStep < poseSteps.length) {
                    updatePoseIndicators(poseSteps[currentStep], false);
                }
            }
        }
    }

    requestAnimationFrame(detectFace);
}


function stopCamera(resetProgress = true) {

    running = false;
    if (resetProgress) resetCaptureProgress();
    faceMissingSince = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();

    if (stream) {

        stream.getTracks().forEach(track => track.stop());

        stream = null;
    }

    video.srcObject = null;

    startButton.disabled = false;
    stopButton.disabled = true;

    cameraMessage.textContent = "Camera stopped";

    instruction.textContent = "Click Start Camera";
}

submitDataset.addEventListener("click", () => {
    submitStudentDataset();
});

startButton.addEventListener("click", startCamera);

stopButton.addEventListener("click", stopCamera);

restartButton.addEventListener("click", () => {
    if (isUploading) return;
    resetCaptureProgress();
    faceMissingSince = null;

    if (running) {
        instruction.textContent = "Look straight at the camera for head-pose calibration";
        cameraMessage.textContent = "Capture restarted from 0%. Complete all steps with the same person.";
        speakInstruction("FRONT");
    } else {
        instruction.textContent = "Capture reset to 0%. Click Start Camera.";
        cameraMessage.textContent = "Capture sequence reset";
    }
});

restartButton.disabled = false;
