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
const cameraFacing = document.getElementById("cameraFacing");
const studentSession = document.getElementById("studentSession");
const registeredUSN = (sessionStorage.getItem("registeredUSN") || "").trim().toUpperCase();
const accessToken = sessionStorage.getItem("studentAccessToken") || "";
const authHeaders = accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
const API_BASE_URL = window.location.port === "5500"
    ? "http://127.0.0.1:8000"
    : "";

const poseSteps = ["FRONT", "LEFT", "RIGHT", "UP", "DOWN"];
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
let isCapturing = false;
let isUploading = false;
let datasetComplete = false;
let captureRevision = 0;
let faceMissingSince = null;
let stepStartedAt = performance.now();
let browserSupabasePromise = null;
let cameraStartPromise = null;
let detectionFrame = null;
let lastSpokenStep = null;
let captureRestorePromise = Promise.resolve();
const captureDbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open("face-capture", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("sessions");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
});

function saveCapturedImages() {
    return captureDbPromise.then(db => new Promise((resolve, reject) => {
        const tx = db.transaction("sessions", "readwrite");
        tx.objectStore("sessions").put({images: capturedImages}, registeredUSN);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
    }));
}

async function restoreCapturedImages() {
    if (!registeredUSN) return;
    try {
        const db = await captureDbPromise;
        const savedState = await new Promise((resolve, reject) => {
            const request = db.transaction("sessions").objectStore("sessions").get(registeredUSN);
            request.onsuccess = () => resolve(request.result || []);
            request.onerror = () => reject(request.error);
        });
        const saved = Array.isArray(savedState) ? savedState : (savedState.images || []);
        for (const image of saved) capturedImages.push(image instanceof File ? image : new File([image.blob], image.name, {type:"image/jpeg"}));
        currentStep = Math.min(capturedImages.length, poseSteps.length);
        renderProgress();
        updatePoseIndicators();
        if (capturedImages.length === 5) instruction.textContent = "All five images captured. Upload your dataset.";
        if (capturedImages.length === 5) document.getElementById("manualEyes").hidden = false;
    } catch (error) {
        console.warn("Could not restore saved captures", error);
    }
}

function renderProgress() {
    const count = Math.min(capturedImages.length, 5);
    const progress = Math.round(count / 5 * 100);
    progressFill.style.width = `${progress}%`;
    progressText.textContent = `${count}/5 images · ${progress}% completed`;
    progressTrack.setAttribute("aria-valuenow", String(progress));
    submitDataset.disabled = count !== 5 || isUploading;
}

if (registeredUSN && accessToken) {
    studentSession.textContent = `Registered student: ${registeredUSN}`;
} else {
    studentSession.textContent = "Register a student first to begin face capture.";
    startButton.disabled = true;
    submitDataset.disabled = true;
    instruction.textContent = "Sign in to continue your face capture.";
}

function resetCaptureProgress() {
    captureRevision++;
    currentStep = 0;
    stableFrames = 0;
    capturedImages.length = 0;
    isCapturing = false;
    neutralPitch = null;
    neutralPitchSamples = [];
    stepStartedAt = performance.now();
    instruction.classList.remove("is-hint");
    progressFill.style.width = "0%";
    progressText.textContent = "0% Completed";
    progressTrack.setAttribute("aria-valuenow", "0");
    submitDataset.disabled = true;
    saveCapturedImages().catch(error => console.warn("Could not save capture state", error));
    renderProgress();
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

async function capturePoseImage(position) {
    if (isCapturing) return;
    isCapturing = true;
    const revision = captureRevision;

    try {
        poseInstruction.textContent = `Capturing ${position.toLowerCase()}...`;
        captureCanvas.width = video.videoWidth;
        captureCanvas.height = video.videoHeight;

        if (!captureCanvas.width || !captureCanvas.height) {
            throw new Error("Camera image is not ready. Please try the pose again.");
        }

        if (revision !== captureRevision) throw new Error("Capture was reset. Try again.");
        captureContext.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height);
        if (!checkLighting(captureCanvas) || !checkBlur(captureCanvas)) throw new Error("Improve lighting or image clarity and try again.");
        const snapshotFaces = faceLandmarker.detectForVideo(captureCanvas, performance.now()).faceLandmarks;
        if (snapshotFaces.length !== 1 || detectHeadPosition(snapshotFaces[0]) !== position) {
            throw new Error(`Hold the ${position.toLowerCase()} pose and try again.`);
        }
        const blob = await new Promise(resolve => captureCanvas.toBlob(resolve, "image/jpeg", 0.92));
        if (!blob) throw new Error("Could not save this image. Please try again.");
        if (revision !== captureRevision) throw new Error("Capture was reset. Start the sequence again.");
        capturedImages.push(new File([blob], `${position.toLowerCase()}.jpg`, {type:"image/jpeg"}));
        await saveCapturedImages();
        renderProgress();
        // Load the browser-safe Supabase client while the student completes the remaining poses.
        void getBrowserSupabaseClient().catch(() => {});
    } finally {
        isCapturing = false;
    }
}

async function submitStudentDataset() {
    if (isUploading) return;
    if (!registeredUSN) {
        poseInstruction.textContent = "Register a student before uploading a dataset.";
        return;
    }
    if (capturedImages.length !== 5 || currentStep !== poseSteps.length) {
        poseInstruction.textContent = "Capture all five face positions first.";
        return;
    }

    isUploading = true;
    submitDataset.disabled = true;
    submitDataset.textContent = "Uploading…";
    restartButton.disabled = true;
    poseInstruction.textContent = "Uploading student details and face images...";

    try {
        const sessionResponse = await fetch(
            `${API_BASE_URL}/upload-dataset/${encodeURIComponent(registeredUSN)}`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json", ...authHeaders },
                body: JSON.stringify({ filenames: capturedImages.map((file) => file.name) })
            }
        );
        const uploadSession = await sessionResponse.json();
        if (!sessionResponse.ok) {
            throw new Error(uploadSession.detail || "Could not prepare secure image uploads.");
        }

        const supabase = await getBrowserSupabaseClient();
        const filesByName = new Map(capturedImages.map((file) => [file.name, file]));

        if (uploadSession.already_complete) {
            datasetComplete = true;
            poseInstruction.textContent = "Your face dataset has already been captured and submitted successfully!";
            stopCamera(false);
            submitDataset.hidden = true;
            restartButton.hidden = true;
            return;
        }
        let completedUploads = uploadSession.uploaded.length;
        const uploadResults = await Promise.allSettled(uploadSession.uploads.map(async upload => {
            const file = filesByName.get(upload.filename);
            if (!file) throw new Error(`Missing captured image: ${upload.filename}`);
            let lastError;
            for (let attempt = 0; attempt < 2; attempt++) {
                let permission = upload;
                if (attempt > 0) {
                    const refresh = await fetch(`${API_BASE_URL}/upload-dataset/${encodeURIComponent(registeredUSN)}`, {
                        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders },
                        body: JSON.stringify({ filenames: capturedImages.map(image => image.name) })
                    });
                    const refreshed = await refresh.json();
                    if (!refresh.ok) throw new Error(refreshed.detail || "Could not refresh upload permission.");
                    permission = refreshed.uploads.find(item => item.filename === upload.filename);
                    if (!permission) return;
                }
                const { error } = await supabase.storage.from(uploadSession.bucket)
                    .uploadToSignedUrl(permission.path, permission.token, file, { contentType: "image/jpeg", upsert: true });
                if (!error) return;
                lastError = error;
            }
            throw new Error(`Could not upload ${upload.filename}: ${lastError.message}`);
        }).map(promise => promise.finally(() => {
            completedUploads++;
            poseInstruction.textContent = `Uploading student details and face images… ${Math.round(completedUploads / 5 * 100)}% (${completedUploads}/5)`;
        })));
        const failedUpload = uploadResults.find(result => result.status === "rejected");
        if (failedUpload) throw failedUpload.reason;

        poseInstruction.textContent = "Confirming all images were saved...";
        const completeResponse = await fetch(
            `${API_BASE_URL}/upload-dataset/${encodeURIComponent(registeredUSN)}/complete`,
            { method: "POST", headers: authHeaders }
        );
        const result = await completeResponse.json();
        if (!completeResponse.ok) {
            throw new Error(result.detail || "Dataset upload could not be confirmed.");
        }

        stopCamera(false);
        datasetComplete = true;
        poseInstruction.textContent = "Student registration and all 5 face images uploaded successfully!";
        cameraMessage.textContent = `${result.image_count} images saved for ${result.usn}.`;
        submitDataset.disabled = true;
        submitDataset.hidden = true;
        restartButton.hidden = true;
        const db = await captureDbPromise;
        await new Promise(resolve => {
            const tx = db.transaction("sessions", "readwrite");
            tx.objectStore("sessions").delete(registeredUSN);
            tx.oncomplete = resolve;
        });
    } catch (error) {
        console.error("Student dataset upload failed", error);
        poseInstruction.textContent = `Upload failed. Please try again. ${error.message || ""}`;
        submitDataset.disabled = false;
    } finally {
        isUploading = false;
        restartButton.disabled = false;
        submitDataset.textContent = "Upload Database";
    }
}

async function getBrowserSupabaseClient() {
    if (!browserSupabasePromise) {
        browserSupabasePromise = (async () => {
            const response = await fetch(`${API_BASE_URL}/public-config`);
            const config = await response.json();
            if (!response.ok) {
                throw new Error(config.detail || "Supabase browser configuration is unavailable.");
            }

            const { createClient } = await import("https://esm.sh/@supabase/supabase-js@2");
            return createClient(config.supabase_url, config.supabase_publishable_key);
        })();
    }

    return browserSupabasePromise;
}

function speakInstruction(step) {
    if (!voiceGuidance.checked || !("speechSynthesis" in window)) return;

    const phrases = { RIGHT: "Move right", LEFT: "Move left", UP: "Look up", DOWN: "Look down", FRONT: "Look straight", EYES: "Close your eyes", COMPLETE: "Capture completed" };
    const phrase = phrases[step];
    if (!phrase) return;

    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(phrase));
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
            numFaces: 1
        }
    );
}

async function startCamera() {
    if (cameraStartPromise) return cameraStartPromise;
    if (stream?.getTracks().some(track => track.readyState === "live")) return;
    cameraStartPromise = (async () => {
        await captureRestorePromise;
        startButton.disabled = true;
        cameraMessage.textContent = "Starting camera and face detection…";
        instruction.textContent = "Loading face detection…";
        let acquiredStream;
        try {
            if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser does not support camera access. Use an updated browser over HTTPS.");
            const cameraPromise = navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: cameraFacing.value || "user" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false
            }).then(cameraStream => { acquiredStream = cameraStream; return cameraStream; });
            const previewPromise = cameraPromise.then(async cameraStream => {
                stream = cameraStream;
                for (const track of cameraStream.getTracks()) {
                    track.addEventListener("ended", () => {
                        if (stream !== cameraStream || !running) return;
                        running = false;
                        if (detectionFrame !== null) cancelAnimationFrame(detectionFrame);
                        detectionFrame = null;
                        stream = null;
                        video.srcObject = null;
                        startButton.disabled = false;
                        stopButton.disabled = true;
                        cameraMessage.textContent = "Camera interrupted. Progress saved. Start the camera to continue.";
                    }, {once:true});
                }
                video.srcObject = cameraStream;
                await video.play();
                running = true;
                neutralPitch = null;
                neutralPitchSamples = [];
                startButton.disabled = true;
                stopButton.disabled = false;
                cameraMessage.textContent = "Camera is active; starting face detection…";
                return cameraStream;
            });
            const modelPromise = faceLandmarker ? Promise.resolve() : loadFaceModel();
            // Avoid an unhandled rejection if camera permission fails before the model load finishes.
            modelPromise.catch(() => {});
            const cameraStream = await previewPromise;
            await modelPromise;
            acquiredStream = cameraStream;
            if (!running || stream !== cameraStream) return;
            cameraMessage.textContent = "Camera and face detection are ready";
            if (currentStep < poseSteps.length) {
                instruction.textContent = "Look straight";
                speakInstruction("FRONT");
                lastSpokenStep = "FRONT";
            }
            detectFace();
        } catch (error) {
            acquiredStream?.getTracks().forEach(track => track.stop());
            stream = null;
            startButton.disabled = !registeredUSN;
            stopButton.disabled = true;
            instruction.textContent = "Camera could not start. Check permission and try again.";
            cameraMessage.textContent = error.name === "NotAllowedError" ? "Camera permission was denied. Allow camera access in browser settings." : (error.message || "Camera is unavailable.");
            console.error("Camera initialization failed", error);
        }
    })().finally(() => { cameraStartPromise = null; });
    return cameraStartPromise;
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
    const expected = poseSteps[currentStep];
    instruction.textContent = ({FRONT:"Look straight",LEFT:"Move left",RIGHT:"Move right",UP:"Look up",DOWN:"Look down"}[expected]);
    if (expected && lastSpokenStep !== expected) {
        speakInstruction(expected);
        lastSpokenStep = expected;
    }
    return true;
}

function detectHeadPosition(landmarks) {
    const { yaw, pitch } = getHeadPoseMetrics(landmarks);
    const verticalDelta = pitch - (neutralPitch ?? 0.365);
    const poseThreshold = 0.045;

    // The preview is mirrored for the student; invert camera-space yaw so
    // physical left/right movement matches the spoken instruction.
    if (yaw < -0.15) return "RIGHT";
    if (yaw > 0.15) return "LEFT";
    if (verticalDelta < -poseThreshold) return "UP";
    if (verticalDelta > poseThreshold) return "DOWN";
    return "FRONT";
}

async function updateProgress(position) {
    if (currentStep >= poseSteps.length || isCapturing || isUploading) return;

    const expected = poseSteps[currentStep];
    let completed = false;
    if (position === expected) {
        instruction.classList.remove("is-hint");
        stableFrames++;
        poseInstruction.textContent = `Hold still… ${Math.min(stableFrames, 8)}/8`;
        updatePoseIndicators(expected, true);
        if (stableFrames >= 8) completed = true;
    } else {
        stableFrames = 0;
        poseInstruction.textContent = ({FRONT:"Look straight",LEFT:"Move left",RIGHT:"Move right",UP:"Look up",DOWN:"Look down"}[expected]);
        updatePoseIndicators(expected, false);
    }

    if (completed) {
        try {
            await capturePoseImage(expected);
        } catch (error) {
            stableFrames = 0;
            updatePoseIndicators(expected, false);
            poseInstruction.textContent = error.message || "Capture failed. Please try the pose again.";
            return;
        }

        currentStep++;
        stableFrames = 0;
        stepStartedAt = performance.now();
        instruction.classList.remove("is-hint");
        updatePoseIndicators();
        renderProgress();

        if (currentStep === poseSteps.length) {
            poseInstruction.textContent = "Capture completed";
            document.getElementById("manualEyes").hidden = false;
            speakInstruction("EYES");
            submitDataset.disabled = capturedImages.length !== 5;
        } else {
            const next = poseSteps[currentStep];
            poseInstruction.textContent = ({FRONT:"Look straight",LEFT:"Move left",RIGHT:"Move right",UP:"Look up",DOWN:"Look down"}[next]);
            if (next !== lastSpokenStep) { speakInstruction(next); lastSpokenStep = next; }
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
            if (currentStep < poseSteps.length) updatePoseIndicators(poseSteps[currentStep], false);
            if (faceMissingSince === null) faceMissingSince = performance.now();
            instruction.textContent = "Move your face into the camera frame";

        } else if (faces.length > 1) {

            faceMissingSince = null;
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

                if (neutralPitch === null && currentStep < 5) {
                    calibrateNeutralPitch(faces[0]);
                } else {
                    const position = detectHeadPosition(faces[0]);
                    updateProgress(position);
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

    detectionFrame = requestAnimationFrame(detectFace);
}


function stopCamera(resetProgress = false) {

    running = false;
    if (detectionFrame !== null) cancelAnimationFrame(detectionFrame);
    detectionFrame = null;
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

    if (resetProgress) instruction.textContent = "Capture reset. Click Start Camera.";
    else if (capturedImages.length < 5) instruction.textContent = "Progress saved. Click Start Camera to continue.";
}

submitDataset.addEventListener("click", () => {
    submitStudentDataset();
});

document.getElementById("eyesClosed").addEventListener("click", () => {
    instruction.textContent = "Great! Continue with the next position.";
});
document.getElementById("retryCamera").addEventListener("click", startCamera);
document.getElementById("signOut").addEventListener("click", () => {
    stopCamera(false);
    sessionStorage.removeItem("registeredUSN");
    sessionStorage.removeItem("studentAccessToken");
    window.location.assign("index.html");
});

startButton.addEventListener("click", startCamera);

voiceGuidance.addEventListener("change", () => {
    if (!voiceGuidance.checked && "speechSynthesis" in window) window.speechSynthesis.cancel();
});

cameraFacing.addEventListener("change", async () => {
    if (!running || isUploading || isCapturing) return;
    stopCamera(false);
    await startCamera();
});

stopButton.addEventListener("click", stopCamera);

restartButton.addEventListener("click", () => {
    if (isUploading || datasetComplete) return;
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

document.querySelectorAll(".pose-status[data-pose]").forEach((button) => {
    button.addEventListener("click", async () => {
        const index = poseSteps.indexOf(button.dataset.pose);
        if (index < 0 || index >= capturedImages.length || isUploading || isCapturing) return;
        capturedImages.length = index;
        currentStep = index;
        stableFrames = 0;
        await saveCapturedImages();
        renderProgress();
        updatePoseIndicators();
        instruction.textContent = "Retake " + button.dataset.pose.toLowerCase();
        poseInstruction.textContent = "Look at the highlighted pose";
        if (!running) await startCamera();
    });
});

document.addEventListener("visibilitychange", () => {
    if (document.hidden && "speechSynthesis" in window) window.speechSynthesis.cancel();
    if (document.hidden && running && !isUploading) stopCamera(false);
});

window.addEventListener("orientationchange", () => {
    // Keep the current pose index and saved image blobs; camera is only restarted on explicit action.
    if (running) stopCamera(false);
});

window.addEventListener("pagehide", () => {
    if (stream) stream.getTracks().forEach(track => track.stop());
});

restartButton.disabled = false;
captureRestorePromise = restoreCapturedImages();
if (!registeredUSN || !accessToken) window.setTimeout(() => window.location.assign("index.html"), 1200);
