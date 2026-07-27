import React, { useEffect, useMemo, useState } from "react";
import { updatePassword } from "firebase/auth";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import "./resetPassword.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

function getPasswordStrength(password) {
    let score = 0;

    if (password.length >= 8) score += 1;
    if (password.length >= 12) score += 1;
    if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 1;
    if (/\d/.test(password)) score += 1;
    if (/[^A-Za-z0-9]/.test(password)) score += 1;

    if (!password) {
        return {
            score: 0,
            label: "Enter a password",
            className: "",
        };
    }

    if (score <= 1) {
        return {
            score: 1,
            label: "Weak",
            className: "reset-strength--weak",
        };
    }

    if (score <= 3) {
        return {
            score: 2,
            label: "Good",
            className: "reset-strength--good",
        };
    }

    return {
        score: 3,
        label: "Strong",
        className: "reset-strength--strong",
    };
}

function getResetErrorMessage(error) {
    switch (error?.code) {
        case "auth/requires-recent-login":
            return "Your session has expired. Please log in again before changing your password.";

        case "auth/weak-password":
            return "Choose a stronger password with at least 8 characters.";

        case "auth/network-request-failed":
            return "Unable to connect. Check your internet connection and try again.";

        default:
            return error?.message || "Could not reset your password.";
    }
}

function ResetPasswordPage() {
    const navigate = useNavigate();
    const location = useLocation();
    const { currentUser, authLoading } = useAuth();

    const [password, setPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [showConfirmPassword, setShowConfirmPassword] = useState(false);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);

    const destination = location.state?.destination || "/";

    const passwordStrength = useMemo(
        () => getPasswordStrength(password),
        [password],
    );

    const passwordsMatch =
        confirmPassword.length > 0 && password === confirmPassword;

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
                throw new Error(
                    data.error || "Could not complete password reset.",
                );
            }

            await currentUser.getIdToken(true);
            navigate(destination, { replace: true });
        } catch (err) {
            console.error("Password reset failed:", err);
            setError(getResetErrorMessage(err));
        } finally {
            setLoading(false);
        }
    }

    if (authLoading) {
        return (
            <main className="reset-page">
                <p className="reset-loading">Loading The Nowl In One...</p>
            </main>
        );
    }

    return (
        <main className="reset-page">
            <section className="reset-layout">
                <div className="reset-visual">
                    <div className="reset-brand">
                        <span className="reset-brand__badge">N</span>

                        <span>
                            <strong>The Nowl In One</strong>
                            <small>Everything we need in one website.</small>
                        </span>
                    </div>

                    <div className="reset-visual__copy">
                        <p className="reset-eyebrow">One last step</p>

                        <h1>Create your new password.</h1>

                        <p>
                            Replace your temporary password before entering The
                            Nowl In One.
                        </p>
                    </div>

                    <div className="reset-security-card">
                        <span className="reset-security-card__icon">✓</span>

                        <div>
                            <strong>Keep your account safe</strong>
                            <p>
                                Use a password that you do not use for another
                                account.
                            </p>
                        </div>
                    </div>

                    <div className="reset-mascots" aria-hidden="true">
                        <span className="reset-mascots__bubble">
                            Make it fat and hard to get through
                        </span>

                        <img
                            src="./src/assets/haru.png"
                            className="reset-mascot reset-mascot--haru"
                            alt=""
                        />

                        <img
                            src="/assets/kotaro.png"
                            className="reset-mascot reset-mascot--kotaro"
                            alt=""
                        />
                    </div>
                </div>

                <div className="reset-panel">
                    <div className="reset-panel__inner">
                        <p className="reset-panel__eyebrow">Password setup</p>

                        <h2>Set a new password</h2>

                        <p className="reset-panel__subtitle">
                            Your new password must contain at least eight
                            characters.
                        </p>

                        <form
                            onSubmit={handleSubmit}
                            className="reset-form"
                        >
                            <label className="reset-label">
                                New password

                                <div className="reset-input-wrapper">
                                    <input
                                        type={
                                            showPassword ? "text" : "password"
                                        }
                                        value={password}
                                        onChange={(event) => {
                                            setPassword(event.target.value);
                                            setError("");
                                        }}
                                        placeholder="Enter a new password"
                                        autoComplete="new-password"
                                        minLength={8}
                                        disabled={loading}
                                        required
                                    />

                                    <button
                                        type="button"
                                        className="reset-password-toggle"
                                        onClick={() =>
                                            setShowPassword((previous) => !previous)
                                        }
                                        disabled={loading}
                                        aria-label={
                                            showPassword
                                                ? "Hide password"
                                                : "Show password"
                                        }
                                    >
                                        {showPassword ? "Hide" : "Show"}
                                    </button>
                                </div>
                            </label>

                            <div className="reset-strength">
                                <div className="reset-strength__header">
                                    <span>Password strength</span>

                                    <strong>{passwordStrength.label}</strong>
                                </div>

                                <div
                                    className={[
                                        "reset-strength__bars",
                                        passwordStrength.className,
                                    ]
                                        .filter(Boolean)
                                        .join(" ")}
                                >
                                    <span
                                        className={
                                            passwordStrength.score >= 1
                                                ? "is-active"
                                                : ""
                                        }
                                    />
                                    <span
                                        className={
                                            passwordStrength.score >= 2
                                                ? "is-active"
                                                : ""
                                        }
                                    />
                                    <span
                                        className={
                                            passwordStrength.score >= 3
                                                ? "is-active"
                                                : ""
                                        }
                                    />
                                </div>
                            </div>

                            <label className="reset-label">
                                Confirm new password

                                <div className="reset-input-wrapper">
                                    <input
                                        type={
                                            showConfirmPassword
                                                ? "text"
                                                : "password"
                                        }
                                        value={confirmPassword}
                                        onChange={(event) => {
                                            setConfirmPassword(
                                                event.target.value,
                                            );
                                            setError("");
                                        }}
                                        placeholder="Enter it again"
                                        autoComplete="new-password"
                                        minLength={8}
                                        disabled={loading}
                                        required
                                    />

                                    <button
                                        type="button"
                                        className="reset-password-toggle"
                                        onClick={() =>
                                            setShowConfirmPassword(
                                                (previous) => !previous,
                                            )
                                        }
                                        disabled={loading}
                                        aria-label={
                                            showConfirmPassword
                                                ? "Hide password confirmation"
                                                : "Show password confirmation"
                                        }
                                    >
                                        {showConfirmPassword ? "Hide" : "Show"}
                                    </button>
                                </div>

                                {confirmPassword && (
                                    <span
                                        className={
                                            passwordsMatch
                                                ? "reset-match reset-match--valid"
                                                : "reset-match reset-match--invalid"
                                        }
                                    >
                                        {passwordsMatch
                                            ? "Passwords match"
                                            : "Passwords do not match"}
                                    </span>
                                )}
                            </label>

                            <div className="reset-requirements">
                                <p>Your password should have:</p>

                                <div className="reset-requirements__grid">
                                    <span
                                        className={
                                            password.length >= 8
                                                ? "is-complete"
                                                : ""
                                        }
                                    >
                                        <i>✓</i>
                                        At least 8 characters
                                    </span>

                                    <span
                                        className={
                                            /[A-Z]/.test(password)
                                                ? "is-complete"
                                                : ""
                                        }
                                    >
                                        <i>✓</i>
                                        An uppercase letter
                                    </span>

                                    <span
                                        className={
                                            /\d/.test(password)
                                                ? "is-complete"
                                                : ""
                                        }
                                    >
                                        <i>✓</i>
                                        A number
                                    </span>

                                    <span
                                        className={
                                            /[^A-Za-z0-9]/.test(password)
                                                ? "is-complete"
                                                : ""
                                        }
                                    >
                                        <i>✓</i>
                                        A symbol
                                    </span>
                                </div>
                            </div>

                            {error && (
                                <div role="alert" className="reset-error">
                                    {error}
                                </div>
                            )}

                            <button
                                type="submit"
                                className="reset-submit"
                                disabled={loading}
                            >
                                {loading
                                    ? "Updating password..."
                                    : "Set new password"}
                            </button>
                        </form>

                        <p className="reset-help">
                            You will be taken to the homepage once your password
                            has been updated.
                        </p>
                    </div>
                </div>
            </section>
        </main>
    );
}

export default ResetPasswordPage;