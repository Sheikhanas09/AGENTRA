"use client";

import { useState } from "react";
import {
  FaCheckCircle,
  FaInfoCircle,
  FaTimes,
  FaUserTie,
  FaCalendarAlt,
  FaClock,
  FaVideo,
  FaMapMarkerAlt,
  FaUsers,
  FaEnvelope,
} from "react-icons/fa";

/* ================= COMPLETED INTERVIEW CARD ================= */
function CompletedInterviewCard({ item, onView }) {
  const { name, position, status, result, feedback } = item;

  return (
    <div
      className="flex flex-col lg:flex-row justify-between gap-6
      p-6 rounded-2xl
      bg-black/40 backdrop-blur-sm
      border border-[#05DC7F]/25
      shadow-[0_0_10px_rgba(5,220,127,0.15)]
      hover:border-[#05DC7F]/45
      transition-all"
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-white text-lg font-semibold">{name}</h3>

          <span
            className="flex items-center gap-2 px-3 py-1 rounded-full
            bg-[#05DC7F]/15 border border-[#05DC7F]/40
            text-[#05DC7F] text-xs font-semibold"
          >
            <FaCheckCircle />
            {status}
          </span>
        </div>

        <p className="text-gray-400">{position}</p>

        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-400">Result:</span>
          <span className="text-white font-semibold">{result}</span>
        </div>

        <p className="text-gray-400 text-sm leading-relaxed max-w-3xl">
          {feedback}
        </p>
      </div>

      <div className="flex items-center">
        <button
          onClick={() => onView(item)}
          className="flex items-center gap-2 px-5 py-2.5
          rounded-xl bg-[#05DC7F]
          hover:bg-[#04c56f]
          text-black font-semibold transition"
        >
          <FaInfoCircle />
          Details
        </button>
      </div>
    </div>
  );
}

/* ================= DETAILS MODAL ================= */
function DetailsModal({ data, onClose }) {
  if (!data) return null;

  const startDate = new Date(data.startTime);
  const endDate = new Date(data.endTime);

  const formattedDate = startDate.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  const formattedStartTime = startDate.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });

  const formattedEndTime = endDate.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-2xl bg-black/90 border border-[#05DC7F]/40 p-6 shadow-[0_0_25px_rgba(5,220,127,0.4)] relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-white"
        >
          <FaTimes />
        </button>

        <h2 className="text-white text-xl font-bold mb-4 text-center">
          Interview Details
        </h2>

        <div className="flex flex-col gap-3 text-gray-300 text-sm">
          <p className="flex items-center gap-2">
            <FaUserTie className="text-[#05DC7F]" />
            <span className="text-white font-semibold">{data.name}</span>
          </p>

          <p className="flex items-center gap-2">
            <FaCalendarAlt className="text-[#05DC7F]" />
            Position:{" "}
            <span className="text-white font-medium">{data.position}</span>
          </p>

          <p className="flex items-center gap-2">
            <FaClock className="text-[#05DC7F]" />
            Date & Time:{" "}
            <span className="text-white font-medium">
              {formattedDate} | {formattedStartTime} - {formattedEndTime}
            </span>
          </p>

          <p className="flex items-center gap-2">
            {data.mode === "Video" || data.type === "Video Call" ? (
              <FaVideo className="text-[#05DC7F]" />
            ) : (
              <FaMapMarkerAlt className="text-[#05DC7F]" />
            )}
            Mode:{" "}
            <span className="text-white font-medium">
              {data.mode || data.type || "N/A"}
            </span>
          </p>

          <p className="flex items-center gap-2">
            <FaUsers className="text-[#05DC7F]" />
            Interviewers:{" "}
            <span className="text-white font-medium">{data.interviewers}</span>
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

/* ================= COMPLETED TAB ================= */
export default function CompletedTab() {
  const interviews = [
    {
      name: "Michael Rodriguez",
      position: "Senior Software Engineer - AI/ML",
      status: "Completed",
      result: "Passed",
      feedback:
        "Strong problem-solving skills, excellent understanding of system design and AI concepts.",
      startTime: "2026-02-03T10:00:00",
      endTime: "2026-02-03T11:00:00",
      mode: "Video",
      interviewers: "John Smith, Emma Watson",
    },
    {
      name: "Sophia Williams",
      position: "UI/UX Designer",
      status: "Completed",
      result: "Under Review",
      feedback:
        "Good design sense and portfolio. Final decision pending from hiring manager.",
      startTime: "2026-02-03T14:00:00",
      endTime: "2026-02-03T15:30:00",
      mode: "In-Person",
      interviewers: "Alice Brown, Mark Lee",
    },
    {
      name: "Daniel Khan",
      position: "Backend Engineer",
      status: "Completed",
      result: "Rejected",
      feedback:
        "Technical fundamentals need improvement, especially in database optimization.",
      startTime: "2026-02-04T13:00:00",
      endTime: "2026-02-04T14:00:00",
      mode: "Video",
      interviewers: "Michael Scott, Sarah Connor",
    },
    {
      name: "Ayesha Malik",
      position: "Frontend Developer",
      status: "Completed",
      result: "Passed",
      feedback: "Excellent React knowledge and clean UI implementation skills.",
      startTime: "2026-02-05T11:00:00",
      endTime: "2026-02-05T12:00:00",
      mode: "Video",
      interviewers: "Emma Watson, John Doe",
    },
  ];

  const itemsPerPage = 2;
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedItem, setSelectedItem] = useState(null);

  const totalPages = Math.ceil(interviews.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentItems = interviews.slice(startIndex, startIndex + itemsPerPage);

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  return (
    <div className="flex flex-col gap-5">
      {currentItems.map((item, index) => (
        <CompletedInterviewCard
          key={index}
          item={item}
          onView={(data) => setSelectedItem(data)}
        />
      ))}

      {/* ===== PAGINATION ===== */}
      <div className="flex justify-end items-center gap-1 text-gray-300 text-xs sm:text-sm mt-4">
        <button
          onClick={() => goToPage(1)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          «
        </button>
        <button
          onClick={() => goToPage(currentPage - 1)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          ‹
        </button>
        <span className="px-1 sm:px-2 py-0.5">
          Page {currentPage} of {totalPages}
        </span>
        <button
          onClick={() => goToPage(currentPage + 1)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          ›
        </button>
        <button
          onClick={() => goToPage(totalPages)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          »
        </button>
      </div>

      {/* ===== DETAILS MODAL ===== */}
      {selectedItem && (
        <DetailsModal
          data={selectedItem}
          onClose={() => setSelectedItem(null)}
        />
      )}
    </div>
  );
}
