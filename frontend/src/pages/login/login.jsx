import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

function getFirebaseErrorMessage(error) {
    switch (error?.code) {
        case "auth/invalid-email":
            return "Please enter a valid email address.";
        case "auth/invalid-credential":
        case "auth/wrong-password":
        case "auth/user-not-found":
            return "The email or password is incorrect.";
        case "auth/too-many-requests":
            return "Too many attempts. Please wait before trying again.";
        case "auth/network-request-failed":
            return "Unable to connect. Check your internet connection.";
        case "auth/operation-not-allowed":
            return "Email and password login has not been enabled in Firebase.";
        default:
            return error?.message || "Authentication failed.";
    }
}

function LoginPage() {
    const navigate = useNavigate();
    const location = useLocation();
    const { login, currentUser, authLoading } = useAuth();

    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);

    const destination = location.state?.from?.pathname || "/";

    useEffect(() => {
        if (!authLoading && currentUser) {
            void redirectAuthenticatedUser(currentUser);
        }
    }, [authLoading, currentUser]);

    async function redirectAuthenticatedUser(user) {
        const tokenResult = await user.getIdTokenResult(true);

        if (tokenResult.claims.mustResetPassword) {
            navigate("/reset-password", {
                replace: true,
                state: { destination },
            });
            return;
        }

        navigate(destination, { replace: true });
    }

    async function handleSubmit(event) {
        event.preventDefault();
        const normalizedEmail = email.trim().toLowerCase();

        if (!normalizedEmail || !password) {
            setError("Please enter your email and password.");
            return;
        }

        setError("");
        setLoading(true);

        try {
            const credential = await login(normalizedEmail, password);
            const user = credential?.user;

            if (!user) {
                throw new Error("Login succeeded but no Firebase user was returned.");
            }

            await redirectAuthenticatedUser(user);
        } catch (err) {
            console.error("Authentication failed:", err);
            setError(getFirebaseErrorMessage(err));
        } finally {
            setLoading(false);
        }
    }

    if (authLoading) {
        return (
            <main style={styles.page}>
                <p style={styles.loadingText}>Loading...</p>
            </main>
        );
    }

    return (
        <main style={styles.page}>
            <section style={styles.card}>
                <div style={styles.header}>
                    <div style={styles.logo}>A</div>
                    <h1 style={styles.title}>Welcome back</h1>
                    <p style={styles.subtitle}>
                        Enter the account details provided by the administrator.
                    </p>
                </div>

                <form onSubmit={handleSubmit} style={styles.form}>
                    <label style={styles.label}>
                        Email
                        <input
                            type="email"
                            value={email}
                            onChange={(event) => setEmail(event.target.value)}
                            placeholder="you@example.com"
                            autoComplete="email"
                            style={styles.input}
                            disabled={loading}
                            required
                        />
                    </label>

                    <label style={styles.label}>
                        Password
                        <input
                            type="password"
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            placeholder="Enter your password"
                            autoComplete="current-password"
                            minLength={6}
                            style={styles.input}
                            disabled={loading}
                            required
                        />
                    </label>

                    {error && <div role="alert" style={styles.error}>{error}</div>}

                    <button
                        type="submit"
                        style={{
                            ...styles.primaryButton,
                            ...(loading ? styles.primaryButtonDisabled : {}),
                        }}
                        disabled={loading}
                    >
                        {loading ? "Please wait..." : "Log in"}
                    </button>
                </form>
            </section>
        </main>
    );
}

const styles = {
    page: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px", background: "#f5f7fb", fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" },
    card: { width: "100%", maxWidth: "400px", padding: "36px", background: "#ffffff", border: "1px solid #e5e7eb", borderRadius: "18px", boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)", boxSizing: "border-box" },
    header: { marginBottom: "28px", textAlign: "center" },
    logo: { width: "44px", height: "44px", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 18px", borderRadius: "12px", background: "#111827", color: "#ffffff", fontSize: "20px", fontWeight: "700" },
    title: { margin: 0, color: "#111827", fontSize: "26px", fontWeight: "700", letterSpacing: "-0.03em" },
    subtitle: { margin: "8px 0 0", color: "#6b7280", fontSize: "14px", lineHeight: 1.6 },
    form: { display: "flex", flexDirection: "column", gap: "18px" },
    label: { display: "flex", flexDirection: "column", gap: "7px", color: "#374151", fontSize: "14px", fontWeight: "600" },
    input: { width: "100%", padding: "12px 13px", border: "1px solid #d1d5db", borderRadius: "10px", outline: "none", background: "#ffffff", color: "#111827", fontSize: "15px", boxSizing: "border-box" },
    error: { padding: "11px 12px", border: "1px solid #fecaca", borderRadius: "9px", background: "#fef2f2", color: "#b91c1c", fontSize: "13px", lineHeight: 1.5 },
    primaryButton: { width: "100%", padding: "12px 16px", border: "none", borderRadius: "10px", background: "#111827", color: "#ffffff", cursor: "pointer", fontSize: "15px", fontWeight: "600" },
    primaryButtonDisabled: { cursor: "not-allowed", opacity: 0.65 },
    loadingText: { color: "#6b7280", fontSize: "15px" },
};

export default LoginPage;