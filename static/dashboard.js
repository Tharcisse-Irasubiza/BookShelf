let results = [];
let library = [];
let view = "search";

function el(id) {
    return document.getElementById(id);
}

function showMessage(text, isError) {
    el("message").textContent = text;
    el("message").className = isError ? "error" : "muted";
}

//check user login

async function api(path, options) {
    let response;
    try {
        response = await fetch(path, options);
    } catch (error) {
        throw new Error("Cannot reach the server. Check your internet connection.");
    }
    let data;
    try {
        data = await response.json();
    } catch (err) {
        throw new Error("The server sent an invalid response. Please try again.");
    }
    el("server").textContent = data.server || "-";
    if (response.status === 401) {
        location.href = "/";
        throw new Error("Please log in.")
    }
    if (!response.ok) throw new Error(data.error || "Something went wrong.");
    return data; 
}

function sendJson(path, body) {
    return api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
}

// stats and chart
function updateStats() {
    const rated = library.filter((b) => b.rating);
    const pages = library.reduce((sum, b) => sum + (b.pages || 0), 0);
    const counts = {};
    library.forEach((b) => {
        counts[b.category] = (counts[b.category] || 0) +1;
    });

    el("count").textContent = library.length;
    el("stat-saved").textContent = library.length;
    el("stat-pages").textContent = pages.toLocalString();
    el("stat-rating").textContent = rated.length
      ? (rated.reduce((sum, b) => sum + b.rating, 0) / rated.length).toFixed(1) + " / 5"
      : "-";
    el("stat-categories").textContent = Object.keys(counts).length;
    
    const chart = el("chart");
    chart.innerHTML = "";
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) {
        chart.textContent = "Save some books to see your reading mix here.";
        chart.className = "muted";
        return;
    }
    chart.className = "";
    const max = entries[0][1];
    entries.forEach(([name, total]) => {
        const row = document.createElement("div");
        row.className = "bar-row";
        const label = document.createElement("span");
        label.textContent = name;
        label.title = name;
        const track = document.createElement("div");
        track.className = "bar-track";
        const bar = document.createElement("div");
        bar.className = "bar";
        bar.style.width = (total / max) * 100 + "%";
        track.appendChild(bar);
        const number = document.createElement("span");
        number.textContent = total;
        row.append(label, track, number);
        chart.appendChild(row);
    });
}

// books
async function searchBooks() {
    const query = el("query").value.trim();
    if (!query) {
        showMessage("Please type a title, author or topic.", true);
        return;
    }
    showMessage("searching...");
    try {
        results = (await api("/api/books?q=" + encodeURIComponent(query))).books;
        showMessage("");
    }   catch (err) {
        results = [];
        showMessage(err.message, true);
    }
    fillCategories();
    render();
}

function fillCategories() {
    const select = el("category");
    const books = view === "search" ? results : library;
    const categories = [...new Set(books.map((b) => b.category))].sort();
    select.innerHTML = "<option value=\"\">All categories</option>";
    categories.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        select.appendChild(option);
    });
}

function isSaved(book) {
    return library.some((b) => b.id === book.id);
}

async function toggleSave(book) {
    try {
        if (isSaved(book)) {
            await api("/api/library?id=" + encodeURIComponet(book.id), { method: "DELETE" });
            library = library.filter((b) => b.id !== book.id);
        }   else {
            await sendJson("/api/library", book);
            library.push(book);
        }
        showMessage("");
    }   catch (err) {
        showMessage(err.message, true);
        return;
    }
    updateStats();
    if (view === "library") fillCategories();
    render();
}

function addText(parent, tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.appendChild(node);
}

function render() {
    const category = el("category").value;
    const sort = el("sort").value;
    const books = (view === "search" ? results : library)
       .filter((b) => !category || b.category === category);
    
    if (sort === "title") books.sort((a, b) => a.title.localeCompare(b.title));
    if (sort === "newest") books.sort((a, b) => (b.year || 0) - (a.year || 0));
    if (sort === "oldest") books.sort((a, b) => (a.year || 9999) - (b.year || 9999));
    if (sort === "rating") books.sort((a, b) => b.rating - a.rating);

    const box = el("books");
    box.innerHTML = "";
    books.forEach((book) => {
        const card = document.createElement("div");
        card.className = "book";
        
        const cover = document.createElement("div");
        cover.className = "cover";
        if (book.cover) cover.style.backgroundImage = "url(" + JSON.stringify(book.cover) + ")";
        card.appendChild(cover);

        const details = document.createElement("div");
        addText(details, "h3", book.title);
        addText(details, "p", book.authors);
        addText(details, "p", (book.year || "Year unknown") + " . " + book.category);
        addText(details, "p", (book.rating ? "★ " + book.rating + " / 5" : "No rating") + 
            (book.pages ? " . " + book.pages + " pages" : ""));
        
        const actions = document.createElement("div");
        actions.className = "actions";
        const button = document.createElement("button");
        button.textContent = isSaved(book) ? "Remove" : "Save";
        button.className = isSaved(book) ? "ghost" : "";
        button.addEventListener("click", () => toggleSave(book));
        actions.appendChild(button);

        if (book.link && book.link.startsWith("https://")) {
            const link = document.createElement("a");
            link.href = book.link;
            link.target = "_blank";
            link.rel = "noopener";
            link.textContent = "More info";
            actions.appendChild(link);
        }

        details.appendChild(actions);
        card.appendChild(details);
        box.appendChild(card);
    });

    if (el("message").className === "error") return;
    if (view === "library" && library.length === 0) {
        showMessage("Your library is empty. Find books and save them here.");
    }  else if (books.length === 0 && results.length > 0) {
        showMessage("No books in this category.");
    }  else if (books.length > 0) {
        showMessage("Showing " + books.length + " books.");
    }  else if (view === "search") {
        showMessage("No books found. Try another search.");
    }
}

function switchView(name) {
    view = name;
    el("tab-search").classList.toggle("active", name === "search");
    el("tab-library").classList.toggle("active", name === "library");
    el("search-form").style.display = name === "search" ? "flex" : "none";
    showMessage("");
    fillCategories();
    render();
}

// start
async function start() {
    try {
        el("who").textContent = (await api("/api/me")).username;
        library = (await api("/api/library")).books;
    }   catch (err) {
        showMessage(err.message, true);
        return;
    }
    updateStats();
    el("query").value = "software engineering";
    searchBooks();
}

el("logout-btn").addEventListener("click", async () => {
    try {
        await sendJson("/api/logout", {});
    }   catch (err) {
        // go to login page if server can't be reached
    }
    location.href = "/";
});
el("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    searchBooks();
});
el("category").addEventListener("change", render);
el("sort").addEventListener("change", render);
el("tab-search").addEventListener("click", () => switchView("search"));
el("tab-library").addEventListener("click", () => switchView("library"));

start();