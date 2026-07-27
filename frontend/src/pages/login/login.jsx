import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import "./login.css";

function getFirebaseErrorMessage(error) {
    switch (error?.code) {
        case "auth/invalid-email": return "Please enter a valid email address.";
        case "auth/invalid-credential":
        case "auth/wrong-password":
        case "auth/user-not-found": return "The email or password is incorrect.";
        case "auth/too-many-requests": return "Too many attempts. Please wait before trying again.";
        case "auth/network-request-failed": return "Unable to connect. Check your internet connection.";
        case "auth/operation-not-allowed": return "Email and password login has not been enabled in Firebase.";
        default: return error?.message || "Authentication failed.";
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
        if (!authLoading && currentUser) void redirectAuthenticatedUser(currentUser);
    }, [authLoading, currentUser]);

    async function redirectAuthenticatedUser(user) {
        const tokenResult = await user.getIdTokenResult(true);
        if (tokenResult.claims.mustResetPassword) {
            navigate("/reset-password", { replace: true, state: { destination } });
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
            if (!user) throw new Error("Login succeeded but no Firebase user was returned.");
            await redirectAuthenticatedUser(user);
        } catch (err) {
            console.error("Authentication failed:", err);
            setError(getFirebaseErrorMessage(err));
        } finally {
            setLoading(false);
        }
    }

    if (authLoading) {
        return <main className="login-page"><p className="login-loading">Loading The Nowl...</p></main>;
    }

    return (
        <main className="login-page">
            <section className="login-layout">
                <div className="login-visual">
                    <div className="login-brand">
                        <span className="login-brand__badge">N</span>
                        <span>
                            <strong>The Nowl In One</strong>
                            <small>Everything we need in one website.</small>
                        </span>
                    </div>

                    <div className="login-visual__copy">
                        <p className="login-eyebrow">Welcome back</p>
                        <h1>Im trying to put literally everything in here</h1>
                        <p>Please use this app I want to learn about what yall like and dislike features u need and all</p>
                    </div>

                    <div className="login-mascots" aria-hidden="true">
                        <span className="login-mascots__bubble">Haru ur a fat ah cat</span>
                        <img src="../../assets/haru.png" className="login-mascot login-mascot--haru" alt="" />
                        <img src="../../assets/kotaro.png" className="login-mascot login-mascot--kotaro" alt="" />
                    </div>
                </div>

                <div className="login-panel">
                    <div className="login-panel__inner">
                        <p className="login-panel__eyebrow">Player login</p>
                        <h2>Welcome back</h2>
                        <p className="login-panel__subtitle">Enter the account details provided by the administrator.</p>

                        <form onSubmit={handleSubmit} className="login-form">
                            <label className="login-label">
                                Email
                                <input
                                    type="email"
                                    value={email}
                                    onChange={(event) => setEmail(event.target.value)}
                                    placeholder="you@example.com"
                                    autoComplete="email"
                                    disabled={loading}
                                    required
                                />
                            </label>

                            <label className="login-label">
                                Password
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(event) => setPassword(event.target.value)}
                                    placeholder="Enter your password"
                                    autoComplete="current-password"
                                    minLength={6}
                                    disabled={loading}
                                    required
                                />
                            </label>

                            {error && <div role="alert" className="login-error">{error}</div>}

                            <button type="submit" className="login-submit" disabled={loading}>
                                {loading ? "Signing in..." : "Log in"}
                            </button>
                        </form>

                        <p className="login-help">Having trouble? Contact your administrator for account access.</p>
                    </div>
                </div>
            </section>
        </main>
    );
}

export default LoginPage;
