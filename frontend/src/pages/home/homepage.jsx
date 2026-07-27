import React from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/authContext.jsx";
import "./homepage.css";

function HomePage() {
    const navigate = useNavigate();
    const { logout, currentUser } = useAuth();

    async function handleLogout() {
        try {
            await logout();
            navigate("/login", { replace: true });
        } catch (error) {
            console.error("Unable to log out:", error);
        }
    }

    return (
        <main className="home-page">
            <div className="home-container">
                <header className="home-header">
                    <div className="home-header-copy">
                        <p className="home-eyebrow">The Nowl</p>

                        <h1 className="home-title">Welcome back</h1>

                        <p className="home-subtitle">
                            {currentUser?.email
                                ? `Signed in as ${currentUser.email}`
                                : "You are signed in"}
                        </p>
                    </div>

                    <button
                        type="button"
                        onClick={handleLogout}
                        className="home-logout-button"
                    >
                        Log out
                    </button>
                </header>

                <section className="home-content">
                    <div className="home-section-heading">
                        <div>
                            <p className="home-section-label">Games</p>
                            <h2>Choose a feature</h2>
                        </div>

                        <p>
                            More tools and games will be added here over time.
                        </p>
                    </div>

                    <button
                        type="button"
                        className="feature-card"
                        onClick={() => navigate("/wordle-ranked")}
                    >
                        <div className="feature-card-icon" aria-hidden="true">
                            <span>W</span>
                        </div>

                        <div className="feature-card-content">
                            <div className="feature-card-heading">
                                <h3>Wordle Ranked</h3>
                                <span className="feature-status">Available</span>
                            </div>

                            <p>
                                Guess the five-letter word in six attempts and
                                build your ranking.
                            </p>

                            <span className="feature-card-link">
                                Play Wordle Ranked
                                <span aria-hidden="true">→</span>
                            </span>
                        </div>
                    </button>
                </section>
            </div>
        </main>
    );
}

export default HomePage;