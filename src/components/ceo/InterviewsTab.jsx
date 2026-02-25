"use client";

import { useState } from "react";
import {
  FaCalendarAlt,
  FaClock,
  FaEnvelope,
  FaVideo,
  FaMapMarkerAlt,
  FaUserTie,
  FaUsers,
  FaTimes,
} from "react-icons/fa";

/* ===== TAB CONTENT COMPONENTS ===== */
import ScheduledTab from "./ScheduledTab";
import CompletedTab from "./CompletedTab";
import NoResponseTab from "./NoResponseTab";

/* ================= DETAILS MODAL ================= */
function DetailsModal({ data, onClose }) {
  if (!data) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4">
      <div className="w-full max-w-lg sm:max-w-xl md:max-w-2xl rounded-2xl bg-black/90 border border-[#05DC7F]/40 p-5 sm:p-6 md:p-8 shadow-[0_0_25px_rgba(5,220,127,0.4)] relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-white"
        >
          <FaTimes />
        </button>

        <h2 className="text-white text-xl sm:text-2xl md:text-3xl font-bold mb-4">
          Interview Details
        </h2>

        <div className="flex flex-col gap-3 text-gray-300 text-sm sm:text-base">
          <p className="flex items-center gap-2">
            <FaUserTie className="text-[#05DC7F]" />
            <span className="text-white font-semibold">{data.name}</span>
          </p>

          <p className="flex items-center gap-2">
            <FaCalendarAlt className="text-[#05DC7F]" />
            Applied For: {data.role}
          </p>

          <p className="flex items-center gap-2">
            <FaClock className="text-[#05DC7F]" />
            Interview Time: {data.time}
          </p>

          <p className="flex items-center gap-2">
            {data.mode === "Video" ? (
              <FaVideo className="text-[#05DC7F]" />
            ) : (
              <FaMapMarkerAlt className="text-[#05DC7F]" />
            )}
            Mode: {data.mode}
          </p>

          <p className="flex items-center gap-2">
            <FaUsers className="text-[#05DC7F]" />
            Interviewers: John Smith, Emma Watson
          </p>

          <p className="flex items-center gap-2">
            <FaEnvelope className="text-[#05DC7F]" />
            Invitation Sent via Email
          </p>
        </div>
      </div>
    </div>
  );
}

/* ================= SCHEDULE CARD ================= */
function ScheduleCard({ data, onDetails }) {
  return (
    <div className="p-4 sm:p-6 rounded-2xl border border-[#05DC7F] bg-[#0b2a1f] shadow-[0_0_20px_rgba(5,220,127,0.25)] flex flex-col lg:flex-row justify-between gap-5 sm:gap-6">
      <div className="flex flex-col gap-3">
        <h3 className="text-white text-lg sm:text-xl font-bold">{data.name}</h3>
        <p className="text-gray-300">{data.role}</p>

        <div className="flex flex-wrap gap-4 sm:gap-6 text-gray-300 text-xs sm:text-sm mt-2">
          <span className="flex items-center gap-2">
            <FaClock className="text-[#05DC7F]" /> {data.time}
          </span>

          <span className="flex items-center gap-2">
            {data.mode === "Video" ? (
              <FaVideo className="text-[#05DC7F]" />
            ) : (
              <FaMapMarkerAlt className="text-[#05DC7F]" />
            )}
            {data.mode}
          </span>

          <span className="flex items-center gap-2">
            <FaEnvelope className="text-[#05DC7F]" /> Email Sent
          </span>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-4 w-full lg:w-auto mt-4 lg:mt-0">
        <button className="px-6 py-3 rounded-xl font-semibold bg-[#05DC7F] text-black flex items-center justify-center gap-2 hover:shadow-[0_0_20px_rgba(5,220,127,0.7)] transition-all">
          <FaVideo /> Join Call
        </button>

        <button
          onClick={() => onDetails(data)}
          className="px-6 py-3 rounded-xl font-semibold border border-[#05DC7F]/40 text-white hover:bg-[#05DC7F]/10 transition-all flex items-center justify-center gap-2"
        >
          <FaCalendarAlt /> Details
        </button>
      </div>
    </div>
  );
}

/* ================= INTERVIEWS TAB ================= */
export default function InterviewsTab() {
  const [activeTab, setActiveTab] = useState("Scheduled");
  const [selected, setSelected] = useState(null);

  const allSchedules = [
    {
      name: "Sarah Chen",
      role: "Senior Software Engineer - AI/ML",
      time: "10:00 AM - 11:00 AM",
      mode: "Video",
    },
    {
      name: "Emily Johnson",
      role: "Product Manager",
      time: "2:00 PM - 3:00 PM",
      mode: "In-Person",
    },
    {
      name: "Michael Lee",
      role: "UI/UX Designer",
      time: "11:00 AM - 12:00 PM",
      mode: "Video",
    },
    {
      name: "Daniel Wilson",
      role: "Backend Developer",
      time: "1:00 PM - 2:00 PM",
      mode: "Video",
    },
  ];

  /* ===== PAGINATION ===== */
  const itemsPerPage = 2;
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = Math.ceil(allSchedules.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentSchedules = allSchedules.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const tabs = ["Scheduled", "Completed", "No Response"];

  return (
    <div className="flex flex-col gap-8">
      {/* ===== TODAY'S SCHEDULE ===== */}
      <div className="flex flex-col gap-6 relative">
        <h2 className="text-white text-xl sm:text-2xl md:text-3xl font-bold">
          Today’s Schedule
        </h2>

        {/* ===== PAGINATION (TOP RIGHT) ===== */}
        <div className="absolute right-0 top-0 flex flex-wrap sm:flex-nowrap items-center gap-2 text-xs sm:text-sm text-gray-300">
          <button
            onClick={() => setCurrentPage(1)}
            className="px-2 py-1 rounded hover:bg-[#05DC7F]/20 hover:text-white transition-all duration-200"
          >
            «
          </button>
          <button
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            className="px-2 py-1 rounded hover:bg-[#05DC7F]/20 hover:text-white transition-all duration-200"
          >
            ‹
          </button>

          <span>
            Page {currentPage} of {totalPages}
          </span>

          <button
            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
            className="px-2 py-1 rounded hover:bg-[#05DC7F]/20 hover:text-white transition-all duration-200"
          >
            ›
          </button>
          <button
            onClick={() => setCurrentPage(totalPages)}
            className="px-2 py-1 rounded hover:bg-[#05DC7F]/20 hover:text-white transition-all duration-200"
          >
            »
          </button>
        </div>

        {currentSchedules.map((item, index) => (
          <ScheduleCard key={index} data={item} onDetails={setSelected} />
        ))}
      </div>

      {/* ===== STATUS TABS ===== */}
      <div className="flex gap-3 overflow-x-auto scrollbar-hide bg-black/30 p-3 rounded-xl backdrop-blur-sm border border-[#05DC7F]/20">
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-5 py-2 rounded-full font-semibold transition-all ${
              activeTab === tab
                ? "bg-[#05DC7F]/20 text-white border border-[#05DC7F]"
                : "text-gray-400 hover:bg-[#05DC7F]/10 hover:text-white"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* ===== TAB CONTENT ===== */}
      <div className="bg-black/30 rounded-2xl p-4 sm:p-6 md:p-8 backdrop-blur-sm border border-[#05DC7F]/20 min-h-[200px] sm:min-h-[260px]">
        {activeTab === "Scheduled" && <ScheduledTab />}
        {activeTab === "Completed" && <CompletedTab />}
        {activeTab === "No Response" && <NoResponseTab />}
      </div>

      {selected && (
        <DetailsModal data={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
