"use client";

import { useState, useEffect, useRef } from "react";
import { Clock, Coffee, MapPin, CalendarDays } from "lucide-react";

export default function EmployeeAttendance() {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);

  const [status, setStatus] = useState("idle");
  const [workingTime, setWorkingTime] = useState(0);
  const [breakTime, setBreakTime] = useState(0);
  const [showCamera, setShowCamera] = useState(false);
  const [location, setLocation] = useState(null);
  const [selectedDate, setSelectedDate] = useState("");

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const rowsPerPage = 3;

  const attendanceHistory = [
    {
      date: "Feb 1, 2026",
      checkin: "09:02 AM",
      checkout: "06:15 PM",
      hours: "9h 13m",
      status: "Present",
    },
    {
      date: "Jan 31, 2026",
      checkin: "08:55 AM",
      checkout: "06:00 PM",
      hours: "9h 5m",
      status: "Present",
    },
    {
      date: "Jan 30, 2026",
      checkin: "09:30 AM",
      checkout: "06:30 PM",
      hours: "9h 0m",
      status: "Late",
    },
    {
      date: "Jan 29, 2026",
      checkin: "09:00 AM",
      checkout: "06:10 PM",
      hours: "9h 10m",
      status: "Present",
    },
    {
      date: "Jan 28, 2026",
      checkin: "-",
      checkout: "-",
      hours: "-",
      status: "Absent",
    },
  ];

  const OFFICE_LOCATION = { lat: 33.6007, lng: 73.0679 };
  const OFFICE_RADIUS = 200;
  const DEV_MODE = true;

  /* ================= TIMER ================= */
  useEffect(() => {
    let interval;
    if (status === "working") {
      interval = setInterval(() => setWorkingTime((prev) => prev + 1), 1000);
    }
    if (status === "break") {
      interval = setInterval(() => setBreakTime((prev) => prev + 1), 1000);
    }
    return () => clearInterval(interval);
  }, [status]);

  const formatTime = (sec) => {
    const h = Math.floor(sec / 3600)
      .toString()
      .padStart(2, "0");
    const m = Math.floor((sec % 3600) / 60)
      .toString()
      .padStart(2, "0");
    const s = (sec % 60).toString().padStart(2, "0");
    return `${h}:${m}:${s}`;
  };

  const getDistance = (lat1, lon1, lat2, lon2) => {
    const R = 6371e3;
    const φ1 = (lat1 * Math.PI) / 180;
    const φ2 = (lat2 * Math.PI) / 180;
    const Δφ = ((lat2 - lat1) * Math.PI) / 180;
    const Δλ = ((lon2 - lon1) * Math.PI) / 180;
    const a =
      Math.sin(Δφ / 2) ** 2 +
      Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
  };

  const fetchLocation = () =>
    new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const coords = {
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
          };
          setLocation(coords);
          const distance = getDistance(
            coords.lat,
            coords.lng,
            OFFICE_LOCATION.lat,
            OFFICE_LOCATION.lng,
          );

          if (!DEV_MODE && distance > OFFICE_RADIUS) {
            alert("You are outside office location");
            reject();
            return;
          }
          resolve(coords);
        },
        (err) => {
          alert("Location permission required");
          reject(err);
        },
        { enableHighAccuracy: true, timeout: 10000 },
      );
    });

  /* ================= CAMERA ================= */
  const openCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user" },
      });
      streamRef.current = stream;
      setShowCamera(true);
    } catch {
      alert("Camera permission required");
    }
  };

  useEffect(() => {
    if (showCamera && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
      videoRef.current.play();
    }
  }, [showCamera]);

  const capturePhoto = async () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0);
    const image = canvas.toDataURL("image/png");
    localStorage.setItem("employee_face", image);
    streamRef.current.getTracks().forEach((track) => track.stop());
    setShowCamera(false);
    try {
      await fetchLocation();
      setStatus("working");
    } catch {}
  };

  const handleCheckIn = () => openCamera();
  const startBreak = async () => {
    try {
      await fetchLocation();
      setStatus("break");
    } catch {}
  };
  const resumeWork = async () => {
    try {
      await fetchLocation();
      setStatus("working");
    } catch {}
  };
  const checkOut = async () => {
    try {
      await fetchLocation();
      setStatus("checkedout");
    } catch {}
  };

  /* ================= UI ================= */
  return (
    <div className="w-full min-h-screen bg-[#111] flex flex-col gap-6 p-6">
      {/* ================= ATTENDANCE CARD ================= */}
      <div className="w-full p-6 rounded-2xl border border-[#05DC7F]/25 backdrop-blur-sm shadow-[0_0_10px_rgba(5,220,127,0.15)] flex flex-col items-center gap-4">
        <div className="w-24 h-24 rounded-full flex items-center justify-center bg-[#05DC7F]/10">
          <Clock size={44} className="text-[#05DC7F]" />
        </div>

        <h2 className="text-white text-lg font-semibold">
          {status === "idle" && "Not Checked In"}
          {status === "working" && "Working"}
          {status === "break" && "On Break"}
          {status === "checkedout" && "Checked Out"}
        </h2>

        <div className="text-gray-400 text-sm flex items-center gap-2">
          <MapPin size={16} />
          {location
            ? `${location.lat.toFixed(4)}, ${location.lng.toFixed(4)}`
            : "Location not fetched"}
        </div>

        <div className="text-gray-300 text-sm">
          Working Hours:{" "}
          <span className="text-white ml-2">{formatTime(workingTime)}</span>
        </div>
        <div className="text-gray-300 text-sm">
          Break Time:{" "}
          <span className="text-white ml-2">{formatTime(breakTime)}</span>
        </div>

        <div className="w-full flex flex-col gap-3 mt-2">
          {status === "idle" && (
            <button
              onClick={handleCheckIn}
              className="w-full py-2.5 rounded-lg font-medium bg-[#05DC7F] text-black shadow hover:shadow-lg hover:scale-[1.02] transition-all duration-300"
            >
              Check In
            </button>
          )}
          {status === "working" && (
            <div className="grid grid-cols-2 gap-3 w-full">
              <button
                onClick={startBreak}
                className="flex items-center justify-center gap-2 py-2.5 rounded-lg font-medium bg-yellow-400 text-black shadow hover:shadow-lg hover:scale-[1.03] transition-all duration-300"
              >
                <Coffee size={16} /> Break
              </button>
              <button
                onClick={checkOut}
                className="flex items-center justify-center gap-2 py-2.5 rounded-lg font-medium bg-red-500 text-white shadow hover:shadow-lg hover:scale-[1.03] transition-all duration-300"
              >
                Check Out
              </button>
            </div>
          )}
          {status === "break" && (
            <button
              onClick={resumeWork}
              className="w-full py-2.5 rounded-lg font-medium bg-gray-700 text-white shadow hover:shadow-lg hover:scale-[1.02] transition-all duration-300"
            >
              Resume Work
            </button>
          )}
        </div>
      </div>

      {/* ================= CAMERA POPUP ================= */}
      {showCamera && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50">
          <div className="bg-[#111] p-6 rounded-xl flex flex-col gap-4">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              className="w-96 rounded-lg"
            />
            <canvas ref={canvasRef} className="hidden" />
            <button
              onClick={capturePhoto}
              className="bg-[#05DC7F] text-black py-2 rounded-lg"
            >
              Capture Photo
            </button>
          </div>
        </div>
      )}

      {/* ================= ATTENDANCE HISTORY ================= */}
      <div className="w-full rounded-2xl bg-black/40 backdrop-blur-sm border border-[#05DC7F]/25 p-4 md:p-6">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 md:mb-6 gap-4 md:gap-0">
          <h2 className="text-white text-base md:text-lg font-semibold flex items-center gap-2">
            <CalendarDays className="text-[#05DC7F]" /> Attendance History
          </h2>
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="bg-black/30 text-gray-300 border border-[#05DC7F]/25 rounded-lg px-3 py-1 text-sm"
          />
        </div>

        <div className="overflow-x-auto w-full">
          <table className="min-w-[700px] md:min-w-full w-full border-collapse table-fixed">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Date
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Check-In
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Check-Out
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Working Hours
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {attendanceHistory
                .slice(
                  (currentPage - 1) * rowsPerPage,
                  currentPage * rowsPerPage,
                )
                .map((item, i) => (
                  <tr
                    key={i}
                    className="border-b border-gray-700 hover:bg-[#05DC7F]/10 transition"
                  >
                    <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300 flex items-center gap-2">
                      <CalendarDays size={16} />
                      {item.date}
                    </td>
                    <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300">
                      {item.checkin}
                    </td>
                    <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300">
                      {item.checkout}
                    </td>
                    <td className="py-2 md:py-4 px-3 md:px-4 text-white font-medium">
                      {item.hours}
                    </td>
                    <td className="py-2 md:py-4 px-3 md:px-4">
                      <span
                        className={`px-2 md:px-3 py-1 rounded-full text-xs md:text-sm font-semibold ${
                          item.status === "Present"
                            ? "text-[#05DC7F] border border-[#05DC7F]/40 bg-[#05DC7F]/10"
                            : item.status === "Late"
                              ? "text-yellow-400 border border-yellow-500/40 bg-yellow-500/10"
                              : "text-red-400 border border-red-500/40 bg-red-500/10"
                        }`}
                      >
                        {item.status}
                      </span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        {/* ================= PAGINATION ================= */}
        {/* ================= PAGINATION ================= */}
        <div className="flex flex-wrap gap-1 justify-end items-center mt-4 text-gray-300 text-xs md:text-sm">
          {/* First Page « */}
          <button
            onClick={() => setCurrentPage(1)}
            disabled={currentPage === 1}
            className={`px-2 py-1 rounded hover:bg-[#05DC7F]/20 transition ${
              currentPage === 1 ? "opacity-50 cursor-not-allowed" : ""
            }`}
          >
            «
          </button>

          {/* Previous ‹ */}
          <button
            onClick={() => setCurrentPage((p) => Math.max(p - 1, 1))}
            disabled={currentPage === 1}
            className={`px-2 py-1 rounded hover:bg-[#05DC7F]/20 transition ${
              currentPage === 1 ? "opacity-50 cursor-not-allowed" : ""
            }`}
          >
            ‹
          </button>

          {/* Page info */}
          <span className="px-2 py-1 md:px-3">
            Page {currentPage} of{" "}
            {Math.ceil(attendanceHistory.length / rowsPerPage)}
          </span>

          {/* Next › */}
          <button
            onClick={() =>
              setCurrentPage((p) =>
                Math.min(
                  p + 1,
                  Math.ceil(attendanceHistory.length / rowsPerPage),
                ),
              )
            }
            disabled={
              currentPage === Math.ceil(attendanceHistory.length / rowsPerPage)
            }
            className={`px-2 py-1 rounded hover:bg-[#05DC7F]/20 transition ${
              currentPage === Math.ceil(attendanceHistory.length / rowsPerPage)
                ? "opacity-50 cursor-not-allowed"
                : ""
            }`}
          >
            ›
          </button>

          {/* Last Page » */}
          <button
            onClick={() =>
              setCurrentPage(Math.ceil(attendanceHistory.length / rowsPerPage))
            }
            disabled={
              currentPage === Math.ceil(attendanceHistory.length / rowsPerPage)
            }
            className={`px-2 py-1 rounded hover:bg-[#05DC7F]/20 transition ${
              currentPage === Math.ceil(attendanceHistory.length / rowsPerPage)
                ? "opacity-50 cursor-not-allowed"
                : ""
            }`}
          >
            »
          </button>
        </div>
      </div>
    </div>
  );
}
