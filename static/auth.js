//login page and creating account
const form = document.getElementById("auth-form");
const message = document.getElementById("message");

function show(text, isError) {
    message.textcontent = text;
    message.className = isError ? "error" : "muted";
}

//when loggedin, move to dashboard
fetch("/api/me")
   .then((response) => response.json().then((data) => {
    document.getElementById("server").textContent = data.server || "-";
    if (response.ok) location.href = "dashboard.html";
   }))
   .catch(() => {})

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const action = form.dataset.action;
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const confirm = document.getElementById("confirm");

    if (confirm && confirm.value !== password) {
        show("Passwords do not match.", true);
        return;
    }
    show(action === "login" ? "Logging in..." : "Creating your account....");

    try {
        const response = await fetch("/api/" + action, {
            method: "POST",
            headers: { "Content-Type": "application/json"},
            body: JSON.stringify({ username: username, password: password })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Something went wrong.");
        location.href = "dashboard.html"
    }   catch (err) {
        if (err instanceof TypeError) {
            show("Cannot reach the server. Check internet connection.", true);
        }   else if (err instanceof SyntaxError) {
            show("The server sent an invalid response. Please try again.", true);
        }   else{
            show(err.message, true);
        }
    }
});