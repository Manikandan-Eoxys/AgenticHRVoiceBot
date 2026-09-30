export async function getToken(name) {
    const response = await fetch(
        `http://localhost:8000/getToken?name=${encodeURIComponent(name)}`
    );

    if (!response.ok) {
        throw new Error("Unable to fetch token");
    }

    return response.json();
}