"use client";
import { useState } from "react";

// Import individual tab components
import CreateJobTab from "./CreateJobTab";
import ShortlistedTab from "./ShortlistedTab";
import AllCandidatesTab from "./AllCandidatesTab";
import JobPostsTab from "./JobPostsTab";

export default function Recruitment() {
  const [activeTab, setActiveTab] = useState("Create Job");

  // Shortlisted candidates state
  const [shortlistedCandidates, setShortlistedCandidates] = useState([]);

  // Lift modal state here
  const [selectedJob, setSelectedJob] = useState(null);

  const tabs = ["Create Job", "Shortlisted", "All Candidates", "Job Posts"];

  // Shortlist handler
  const handleShortlist = (candidate) => {
    setShortlistedCandidates((prev) => [
      ...prev,
      {
        name: candidate.name,
        position: candidate.position,
        date: candidate.appliedOn,
        cvUrl: candidate.cvUrl,
      },
    ]);
  };

  return (
    <div className="relative flex flex-col gap-6 sm:gap-8 p-4 sm:p-6">
      {/* ===== TABS ===== */}
      <div className="flex flex-wrap gap-3 sm:gap-4 bg-black/30 p-2 sm:p-3 rounded-xl backdrop-blur-sm border border-[#05DC7F]/20 shadow-[0_0_10px_rgba(5,220,127,0.15)]">
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 sm:px-5 py-2 rounded-full font-semibold text-sm sm:text-base transition-all duration-300
              ${
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
      <div className="bg-black/30 rounded-2xl p-4 sm:p-6 backdrop-blur-sm border border-[#05DC7F]/20 shadow-[0_0_10px_rgba(5,220,127,0.15)] min-h-[250px] sm:min-h-[300px]">
        {activeTab === "Create Job" && <CreateJobTab />}

        {activeTab === "Shortlisted" && (
          <ShortlistedTab shortlistedCandidates={shortlistedCandidates} />
        )}

        {activeTab === "All Candidates" && (
          <AllCandidatesTab onShortlist={handleShortlist} />
        )}

        {activeTab === "Job Posts" && (
          <JobPostsTab setSelectedJob={setSelectedJob} />
        )}
      </div>

      {/* ===== Job Modal (lifted to Recruitment.jsx) ===== */}
      {selectedJob && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center px-2 sm:px-4">
          {/* Background overlay */}
          <div
            className="absolute inset-0 bg-black/70 backdrop-blur-xs"
            onClick={() => setSelectedJob(null)}
          ></div>

          {/* Modal box */}
          <div
            className="relative bg-[#0b0b0b] w-full max-w-md sm:max-w-2xl
                        rounded-2xl border border-[#05DC7F]/30
                        shadow-[0_0_25px_rgba(5,220,127,0.25)]
                        flex flex-col max-h-[90vh]"
          >
            {/* Header */}
            <div className="flex justify-between items-center px-4 sm:px-6 py-3 sm:py-4 border-b border-gray-700">
              <h4 className="text-white text-base sm:text-lg font-bold">
                {selectedJob.title}
              </h4>
              <button
                onClick={() => setSelectedJob(null)}
                className="text-gray-400 hover:text-white text-lg sm:text-xl"
              >
                ✕
              </button>
            </div>

            {/* Scrollable Content */}
            <div
              className="px-4 sm:px-6 py-3 sm:py-4 overflow-y-auto flex-1
                        scrollbar-thin scrollbar-thumb-[#05DC7F] scrollbar-track-gray-800"
            >
              <pre className="text-gray-300 leading-relaxed whitespace-pre-line break-words text-sm sm:text-base">
                {selectedJob.description}
              </pre>
            </div>

            {/* Footer */}
            <div className="flex justify-end px-4 sm:px-6 py-3 sm:py-4 border-t border-gray-700">
              <button
                onClick={() => setSelectedJob(null)}
                className="px-4 sm:px-5 py-2 sm:py-2.5 rounded-lg bg-[#05DC7F]
                          hover:bg-[#04c56f] text-black font-semibold transition text-sm sm:text-base"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
