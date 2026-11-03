
const form = document.getElementById("registrationForm");
const message = document.getElementById("message");
const submitBtn = document.getElementById("submitBtn");
const API_BASE_URL = window.location.port === "5500"
    ? "http://127.0.0.1:8000"
    : "";

form.addEventListener("submit", async function(event) {
    event.preventDefault();

    const studentData = {
        name: document.getElementById("name").value.trim(),
        usn: document.getElementById("usn").value.trim(),
        email: document.getElementById("email").value.trim(),
        mobile: document.getElementById("mobile").value.trim()
    };

    submitBtn.disabled = true;
    message.textContent = "Registering student...";

    try {
        const response = await fetch(
            `${API_BASE_URL}/register`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(studentData)
            }
        );

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.detail || "Registration failed");
        }

        message.textContent = "Registration successful!";
        message.style.color = "green";

        localStorage.setItem("registeredUSN", result.student);
        message.textContent = "Registration successful! Opening face capture...";
        window.location.assign("face_capture.html");

    } catch (error) {
        message.textContent = error.message;
        message.style.color = "red";

    } finally {
        submitBtn.disabled = false;
    }
});
