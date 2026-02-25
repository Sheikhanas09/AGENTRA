"use client";

import { useState } from "react";
import { FaFileAlt, FaCalendarCheck, FaTimes, FaTrash } from "react-icons/fa";

export default function ShortlistedTab() {
  const shortlistedCandidates = [
    {
      name: "Alice Johnson",
      position: "Software Engineer",
      date: "2026-02-01",
      email: "alice@gmail.com",
      cvUrl: "/dummy-cv.pdf",
    },
    {
      name: "Bob Smith",
      position: "UI/UX Designer",
      date: "2026-01-28",
      email: "bob@gmail.com",
      cvUrl: "/dummy-cv.pdf",
    },
    {
      name: "Charlie Brown",
      position: "Data Analyst",
      date: "2026-01-25",
      email: "charlie@gmail.com",
      cvUrl: "/dummy-cv.pdf",
    },
    {
      name: "Charlie Brown",
      position: "Data Analyst",
      date: "2026-01-25",
      email: "charlie@gmail.com",
      cvUrl: "/dummy-cv.pdf",
    },
    {
      name: "Charlie Brown",
      position: "Data Analyst",
      date: "2026-01-25",
      email: "charlie@gmail.com",
      cvUrl: "/dummy-cv.pdf",
    },
    {
      name: "Charlie Brown",
      position: "Data Analyst",
      date: "2026-01-25",
      email: "charlie@gmail.com",
      cvUrl: "/dummy-cv.pdf",
    },
  ];

  const employees = [
    { name: "Ahmed (Tech Lead)", email: "ahmed@company.com" },
    { name: "Sara (HR)", email: "sara@company.com" },
    { name: "Ali (Engineering Manager)", email: "ali@company.com" },
  ];

  /* ===== Pagination ===== */
  const itemsPerPage = 3;
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = Math.ceil(shortlistedCandidates.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;

  const currentCandidates = shortlistedCandidates.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  /* ===== Modal States ===== */
  const [showModal, setShowModal] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [heldBy, setHeldBy] = useState([]);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [openDropdown, setOpenDropdown] = useState(false);

  const openScheduleModal = (candidate) => {
    setSelectedCandidate(candidate);
    setHeldBy([]);
    setDate("");
    setTime("");
    setOpenDropdown(false);
    setShowModal(true);
  };

  const toggleHeldBy = (emp) => {
    setHeldBy((prev) => {
      if (prev.includes(emp)) return prev;
      const updated = prev.length < 2 ? [...prev, emp] : prev;
      if (updated.length === 2) setOpenDropdown(false);
      return updated;
    });
  };

  const removeHeldBy = (emp) => {
    setHeldBy((prev) => prev.filter((e) => e !== emp));
  };

  /* ===== Schedule Handler ===== */
  const handleSchedule = () => {
    if (!date || !time || heldBy.length === 0) {
      alert("⚠️ Please complete all fields before scheduling.");
      return;
    }

    const candidateEmail = selectedCandidate.email;
    const interviewerEmails = heldBy.map((e) => e.email);
    const allEmails = [candidateEmail, ...interviewerEmails];

    alert(
      `✅ Interview Scheduled!\n\nCandidate: ${selectedCandidate.name}\nDate: ${date}\nTime: ${time}\nInterviewers: ${heldBy.map((e) => e.name).join(", ")}\n\nEmails will be sent to:\n${allEmails.join(", ")}`,
    );

    // Here you can later send allEmails + date/time + meet link to backend
    setShowModal(false);
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <h3 className="text-white text-xl sm:text-2xl font-bold">
        Shortlisted Candidates
      </h3>

      {/* ===== Table Header (Desktop only) ===== */}
      <div className="hidden md:grid grid-cols-12 gap-4 px-4 py-2 border-b border-gray-700 text-gray-400 font-semibold">
        <div className="col-span-1">#</div>
        <div className="col-span-3">Candidate</div>
        <div className="col-span-3">Position</div>
        <div className="col-span-3">Date</div>
        <div className="col-span-2 text-right">Actions</div>
      </div>

      {/* ===== Rows ===== */}
      <div className="flex flex-col divide-y divide-gray-800">
        {currentCandidates.map((c, i) => (
          <div
            key={i}
            className="grid grid-cols-1 md:grid-cols-12 gap-2 sm:gap-4 items-center px-3 sm:px-4 py-4 hover:bg-black/20 rounded-lg transition"
          >
            <div className="col-span-1 text-gray-400 text-sm sm:text-base">
              {startIndex + i + 1}
            </div>
            <div className="col-span-3 text-white font-medium text-sm sm:text-base">
              {c.name}
            </div>
            <div className="col-span-3 text-[#05DC7F] font-semibold text-sm sm:text-base">
              {c.position}
            </div>
            <div className="col-span-3 text-white text-sm sm:text-base">
              {c.date}
            </div>

            <div className="col-span-2 flex flex-wrap gap-2 justify-start md:justify-end">
              <button
                onClick={() => window.open(c.cvUrl, "_blank")}
                className="flex items-center gap-2 px-2 sm:px-3 py-1.5 sm:py-2 text-xs sm:text-sm rounded-lg bg-black/40 border border-gray-600 hover:border-[#05DC7F]"
              >
                <FaFileAlt />
                <span className="hidden sm:inline">View CV</span>
              </button>

              <button
                onClick={() => openScheduleModal(c)}
                className="flex items-center gap-2 px-2 sm:px-3 py-1.5 sm:py-2 bg-[#05DC7F] text-black rounded-lg text-xs sm:text-sm font-semibold"
              >
                <FaCalendarCheck />
                <span className="hidden sm:inline">Schedule</span>
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ===== Pagination ===== */}
      <div className="flex flex-wrap justify-end items-center gap-2 mt-2 sm:mt-4 text-gray-300 text-sm sm:text-base">
        <button
          onClick={() => goToPage(1)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          «
        </button>
        <button
          onClick={() => goToPage(currentPage - 1)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          ‹
        </button>
        <span className="px-2 sm:px-3 py-1">
          Page {currentPage} of {totalPages}
        </span>
        <button
          onClick={() => goToPage(currentPage + 1)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          ›
        </button>
        <button
          onClick={() => goToPage(totalPages)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          »
        </button>
      </div>

      {/* ===== Schedule Modal ===== */}
      {showModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="bg-[#1F1F1F] w-full max-w-lg rounded-xl p-6 relative">
            <button
              onClick={() => setShowModal(false)}
              className="absolute right-4 top-4 text-gray-400"
            >
              <FaTimes />
            </button>

            <h4 className="text-white text-lg font-bold mb-4">
              Schedule Interview
            </h4>

            <div className="space-y-4">
              <div>
                <label className="text-gray-400 text-sm">Candidate Email</label>
                <input
                  value={selectedCandidate?.email}
                  disabled
                  className="w-full mt-1 p-2 rounded bg-black/40 text-white border border-gray-700"
                />
              </div>

              <div>
                <label className="text-gray-400 text-sm">Interview Date</label>
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="w-full mt-1 p-2 rounded bg-black/40 text-white border border-gray-700"
                />
              </div>

              <div>
                <label className="text-gray-400 text-sm">Interview Time</label>
                <input
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  className="w-full mt-1 p-2 rounded bg-black/40 text-white border border-gray-700"
                />
              </div>

              <div className="relative">
                <label className="text-gray-400 text-sm">Held By (max 2)</label>
                <div
                  onClick={() => setOpenDropdown(!openDropdown)}
                  className="mt-1 p-2 bg-black/40 border border-gray-700 rounded cursor-pointer text-white"
                >
                  Select interviewers
                </div>

                {openDropdown && (
                  <div className="absolute w-full bg-[#121212] border border-gray-700 rounded mt-1 z-10">
                    {employees.map((emp, i) => (
                      <div
                        key={i}
                        onClick={() => toggleHeldBy(emp)}
                        className="px-3 py-2 hover:bg-[#05DC7F]/20 cursor-pointer text-white text-sm"
                      >
                        {emp.name}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {heldBy.length > 0 && (
                <div className="flex gap-2 flex-wrap">
                  {heldBy.map((emp) => (
                    <div
                      key={emp.email}
                      className="flex items-center gap-2 bg-[#05DC7F]/20 text-white px-3 py-1 rounded-full text-sm"
                    >
                      {emp.name}
                      <FaTrash
                        onClick={() => removeHeldBy(emp)}
                        className="cursor-pointer text-red-400"
                      />
                    </div>
                  ))}
                </div>
              )}

              <button
                onClick={handleSchedule}
                className="w-full bg-[#05DC7F] py-2 rounded font-semibold text-black"
              >
                Schedule Interview
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
