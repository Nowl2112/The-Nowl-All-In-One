import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import ProtectedRoute from "../components/protectedRoute.jsx";
import HomePage from "../pages/home/homepage.jsx";
import LoginPage from "../pages/login/login.jsx";
import WordleRanked from "../pages/wordle/wordleRanked.jsx";
import ResetPasswordPage from "../pages/resetPassword/resetPassword.jsx";
import CalendarPage from "../pages/calendar/calendar.jsx";
import TaskBoardPage from "../pages/taskboard/taskboard.jsx";
import NewsCenter from "../pages/newscenter/newscenter.jsx";


function AppRoutes() {
    return (
        <Routes>
            <Route path="/login" element={<LoginPage />} />

            <Route
                path="/"
                element={
                    <ProtectedRoute>
                        <HomePage />
                    </ProtectedRoute>
                }
            />

            <Route
                path="/wordle-ranked"
                element={
                    <ProtectedRoute>
                        <WordleRanked />
                    </ProtectedRoute>
                }
            />
            <Route path="/news" element={<ProtectedRoute><NewsCenter /></ProtectedRoute>} />

            <Route path="/task-boards" element={<ProtectedRoute><TaskBoardPage /></ProtectedRoute>} />
            <Route
                path="/calendar"
                element={
                    <ProtectedRoute>
                        <CalendarPage />
                    </ProtectedRoute>
                }
            />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    );
}

export default AppRoutes;