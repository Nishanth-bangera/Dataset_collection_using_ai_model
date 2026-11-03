
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

        if (result.dataset_completed) {
            message.textContent = `Your face data has already been captured and sent to Supabase.\nName: ${result.name}\nUSN: ${result.student}\nEmail: ${result.email}\nMobile: ${result.mobile}`;
            message.style.color = "green";
            return;
        }

        localStorage.setItem("registeredUSN", result.student);
        message.textContent = result.existing_student
            ? "Your registration is already saved. Face capture is incomplete, so you can continue now. Opening face capture..."
            : "Registration successful. Opening face capture...";
        message.style.color = "green";
        window.setTimeout(() => window.location.assign("face_capture.html"), 900);

    } catch (error) {
        message.textContent = error.message;
        message.style.color = "red";

    } finally {
        submitBtn.disabled = false;
    }
});
