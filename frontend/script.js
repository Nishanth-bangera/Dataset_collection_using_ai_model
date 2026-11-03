const API_BASE_URL = window.location.port === "5500" ? "http://127.0.0.1:8000" : "";
const message = document.getElementById("message");
const signupForm = document.getElementById("registrationForm");
const signinForm = document.getElementById("signinForm");
let supabasePromise;
let verifiedSignupSession = null;
let verifiedSigninSession = null;

async function supabaseClient() {
  if (!supabasePromise) supabasePromise = (async () => {
    const response = await fetch(`${API_BASE_URL}/public-config`);
    const config = await response.json();
    if (!response.ok) throw new Error(config.detail || "Portal configuration is unavailable.");
    const { createClient } = await import("https://esm.sh/@supabase/supabase-js@2");
    return createClient(config.supabase_url, config.supabase_publishable_key);
  })();
  return supabasePromise;
}

function say(text, error = false) { message.textContent = text; message.className = error ? "error" : "success"; }
function getEmail(id) { return document.getElementById(id).value.trim().toLowerCase(); }
function validUSN(value) { return /^[A-Z0-9]{5,24}$/.test(value.trim().toUpperCase()); }
async function requestOtp(email, button) {
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error("Enter a valid email address.");
  button.disabled = true;
  try {
    const client = await supabaseClient();
    const { error } = await client.auth.signInWithOtp({ email, options: { shouldCreateUser: true } });
    if (error) throw error;
    say("If this address can receive a code, an OTP has been sent. Check your inbox and spam folder.");
    let seconds = 30;
    const original = button.textContent;
    button.textContent = `Resend in ${seconds}s`;
    const timer = setInterval(() => {
      seconds -= 1;
      if (seconds <= 0) { clearInterval(timer); button.textContent = original; button.disabled = false; }
      else button.textContent = `Resend in ${seconds}s`;
    }, 1000);
  } catch (error) { button.disabled = false; throw error; }
}
async function verifyOtp(email, otp) {
  if (!email || !otp.trim()) throw new Error("Enter your email address and OTP.");
  const client = await supabaseClient();
  const { data, error } = await client.auth.verifyOtp({ email, token: otp.trim(), type: "email" });
  if (error || !data.session) throw new Error("OTP could not be verified. Check the code or request a new one.");
  return data.session;
}
async function api(path, { token, ...options } = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) } });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || "The request could not be completed.");
  return result;
}
function setSession(session, usn) {
  sessionStorage.setItem("studentAccessToken", session.access_token);
  sessionStorage.setItem("registeredUSN", usn.trim().toUpperCase());
}
function showStatus(result) {
  if (result.face_capture_status === "completed" && result.dataset_upload_status === "completed") {
    document.getElementById("signupPanel").hidden = true;
    document.getElementById("signinPanel").hidden = true;
    const dl = document.getElementById("studentDetails");
    const fields = [["Student Name", result.student.name], ["USN", result.student.usn], ["Email", result.student.email], ["Registration Status", "Completed"], ["Face Capture Status", "Completed"], ["Dataset Upload Status", "Completed"]];
    dl.innerHTML = fields.map(([key, value]) => `<dt>${key}</dt><dd>${escapeHtml(value)}</dd>`).join("");
    document.getElementById("completedPanel").hidden = false;
    say("Your face dataset has already been captured and submitted successfully!");
    return;
  }
  document.getElementById("signupPanel").hidden = true;
  document.getElementById("signinPanel").hidden = true;
  const incomplete = result.face_capture_status === "in_progress";
  document.getElementById("progressHeading").textContent = incomplete ? "Your face capture is incomplete. Continue from where you stopped." : "Your registration is complete. Face capture is pending.";
  document.getElementById("progressMessage").textContent = incomplete ? "Your saved capture progress will be restored on this device." : "You can start your five image face capture when your camera is ready.";
  document.getElementById("continueCapture").textContent = incomplete ? "Resume Face Capture" : "Start Face Capture";
  document.getElementById("progressPanel").hidden = false;
  say("");
}
function escapeHtml(value) { const span = document.createElement("span"); span.textContent = String(value ?? ""); return span.innerHTML; }

document.getElementById("signupTab").addEventListener("click", () => {
  document.getElementById("signupPanel").hidden = false; document.getElementById("signinPanel").hidden = true;
  document.getElementById("signupTab").classList.add("selected"); document.getElementById("signinTab").classList.remove("selected"); say("");
});
document.getElementById("signinTab").addEventListener("click", () => {
  document.getElementById("signinPanel").hidden = false; document.getElementById("signupPanel").hidden = true;
  document.getElementById("signinTab").classList.add("selected"); document.getElementById("signupTab").classList.remove("selected"); say("");
});
document.getElementById("sendSignupOtp").addEventListener("click", async event => { try { const usn = document.getElementById("usn").value.toUpperCase(); if (!validUSN(usn)) throw new Error("Enter a valid USN."); await requestOtp(getEmail("email"), event.currentTarget); } catch (error) { say(error.message, true); } });
document.getElementById("resendSignupOtp").addEventListener("click", async event => { try { await requestOtp(getEmail("email"), event.currentTarget); } catch (error) { say(error.message, true); } });
document.getElementById("verifySignupOtp").addEventListener("click", async () => { try { verifiedSignupSession = await verifyOtp(getEmail("email"), document.getElementById("signupOtp").value); document.getElementById("submitBtn").disabled = false; say("Email verified. Continue to save your registration."); } catch (error) { say(error.message, true); } });
signupForm.addEventListener("submit", async event => {
  event.preventDefault(); const button = document.getElementById("submitBtn");
  if (!verifiedSignupSession) return say("Verify your email before continuing.", true);
  const usn = document.getElementById("usn").value.trim().toUpperCase();
  if (!validUSN(usn)) return say("Enter a valid USN.", true);
  button.disabled = true; say("Saving student registration...");
  try {
    const result = await api("/register", { method: "POST", token: verifiedSignupSession.access_token, body: JSON.stringify({ name: document.getElementById("name").value.trim(), usn, email: getEmail("email") }) });
    setSession(verifiedSignupSession, usn); showStatus(result);
  } catch (error) { say(error.message, true); button.disabled = false; }
});
document.getElementById("sendSigninOtp").addEventListener("click", async event => { try { if (!validUSN(document.getElementById("loginUsn").value)) throw new Error("Enter a valid USN."); await requestOtp(getEmail("loginEmail"), event.currentTarget); } catch (error) { say(error.message, true); } });
document.getElementById("resendSigninOtp").addEventListener("click", async event => { try { await requestOtp(getEmail("loginEmail"), event.currentTarget); } catch (error) { say(error.message, true); } });
document.getElementById("verifySigninOtp").addEventListener("click", async () => {
  try {
    verifiedSigninSession = await verifyOtp(getEmail("loginEmail"), document.getElementById("signinOtp").value);
    const usn = document.getElementById("loginUsn").value.trim().toUpperCase();
    const result = await api(`/sign-in/${encodeURIComponent(usn)}`, { method: "POST", token: verifiedSigninSession.access_token });
    setSession(verifiedSigninSession, usn); showStatus(result);
  } catch (error) { say(error.message || "Sign in could not be verified. Check your details and try again.", true); }
});
document.getElementById("signOut").addEventListener("click", () => { sessionStorage.removeItem("studentAccessToken"); sessionStorage.removeItem("registeredUSN"); location.reload(); });
document.getElementById("continueCapture").addEventListener("click", () => window.location.assign("face_capture.html"));
document.getElementById("progressSignOut").addEventListener("click", () => { sessionStorage.removeItem("studentAccessToken"); sessionStorage.removeItem("registeredUSN"); location.reload(); });
