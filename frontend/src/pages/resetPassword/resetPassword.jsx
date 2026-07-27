import React, { useEffect, useState } from "react";
import { updatePassword } from "firebase/auth";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

function ResetPasswordPage() {
    const navigate = useNavigate();
    const location = useLocation();
    const { currentUser, authLoading } = useAuth();

    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);

    const destination = location.state?.destination || "/";

    useEffect(() => {
        if (!authLoading && !currentUser) {
            navigate("/login", { replace: true });
        }
    }, [authLoading, currentUser, navigate]);

    async function handleSubmit(event) {
        event.preventDefault();

        if (password.length < 8) {
            setError("Use a password with at least 8 characters.");
            return;
        }
        if (password !== confirmPassword) {
            setError("The passwords do not match.");
            return;
        }
        if (!currentUser) {
            setError("Your login session has expired. Please log in again.");
            return;
        }

        setError("");
        setLoading(true);

        try {
            await updatePassword(currentUser, password);

            const idToken = await currentUser.getIdToken();
            const response = await fetch(
                `${API_BASE_URL}/api/auth/complete-password-reset`,
                {
                    method: "POST",
                    headers: {
                        Authorization: `Bearer ${idToken}`,
                        "Content-Type": "application/json",
                    },
                },
            );
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || "Could not complete password reset.");
            }

            await currentUser.getIdToken(true);
            navigate(destination, { replace: true });
        } catch (err) {
            console.error("Password reset failed:", err);
            setError(err?.message || "Could not reset your password.");
        } finally {
            setLoading(false);
        }
    }

    return (
        <main style={styles.page}>
            <section style={styles.card}>
                <h1 style={styles.title}>Create a new password</h1>
                <p style={styles.subtitle}>
                    You must replace the temporary password before continuing.
                </p>

                <form onSubmit={handleSubmit} style={styles.form}>
                    <label style={styles.label}>
                        New password
                        <input
                            type="password"
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            autoComplete="new-password"
                            minLength={8}
                            style={styles.input}
                            disabled={loading}
                            required
                        />
                    </label>

                    <label style={styles.label}>
                        Confirm new password
                        <input
                            type="password"
                            value={confirmPassword}
                            onChange={(event) => setConfirmPassword(event.target.value)}
                            autoComplete="new-password"
                            minLength={8}
                            style={styles.input}
                            disabled={loading}
                            required
                        />
                    </label>

                    {error && <div role="alert" style={styles.error}>{error}</div>}

                    <button type="submit" style={styles.button} disabled={loading}>
                        {loading ? "Updating..." : "Set new password"}
                    </button>
                </form>
            </section>
        </main>
    );
}

const styles = {
    page: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px", background: "#f5f7fb", fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" },
    card: { width: "100%", maxWidth: "420px", padding: "36px", background: "#fff", border: "1px solid #e5e7eb", borderRadius: "18px", boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)" },
    title: { margin: 0, color: "#111827", fontSize: "26px" },
    subtitle: { margin: "10px 0 26px", color: "#6b7280", lineHeight: 1.6 },
    form: { display: "flex", flexDirection: "column", gap: "18px" },
    label: { display: "flex", flexDirection: "column", gap: "7px", color: "#374151", fontSize: "14px", fontWeight: 600 },
    input: { width: "100%", padding: "12px 13px", border: "1px solid #d1d5db", borderRadius: "10px", boxSizing: "border-box" },
    error: { padding: "11px 12px", border: "1px solid #fecaca", borderRadius: "9px", background: "#fef2f2", color: "#b91c1c", fontSize: "13px" },
    button: { width: "100%", padding: "12px 16px", border: 0, borderRadius: "10px", background: "#111827", color: "#fff", cursor: "pointer", fontWeight: 600 },
};

export default ResetPasswordPage;