import cv2
import numpy as np
import os
import requests

# --- CONFIGURATION ---
CAMERA_INDEX = 1 
FINAL_SIZE = 800

# Feed Cropping Offsets (Applied after resizing to 1024x768)
# Increase these values to crop more of the background out of the live feed
OFFSET_TOP = 220
OFFSET_BOTTOM = 110
OFFSET_LEFT = 300
OFFSET_RIGHT = 400

def nothing(x): pass

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def get_intersection(line1, line2):
    vx1, vy1, x1, y1 = line1.flatten()
    vx2, vy2, x2, y2 = line2.flatten()
    
    A1, B1 = -vy1, vx1
    C1 = A1 * x1 + B1 * y1
    A2, B2 = -vy2, vx2
    C2 = A2 * x2 + B2 * y2
    
    det = A1 * B2 - A2 * B1
    if abs(det) < 1e-6: return None 
        
    Px = (C1 * B2 - C2 * B1) / det
    Py = (A1 * C2 - A2 * C1) / det
    return [Px, Py]

script_dir = os.path.dirname(os.path.abspath(__file__))
squares_dir = os.path.join(script_dir, 'squares')

# --- UI SETUP ---
cv2.namedWindow("Live Tuning")
cv2.resizeWindow("Live Tuning", 500, 350)
cv2.createTrackbar("Saturation", "Live Tuning", 97, 255, nothing)
cv2.createTrackbar("Min Sq Size", "Live Tuning", 9490, 15000, nothing)
cv2.createTrackbar("Max Sq Size", "Live Tuning", 25000, 50000, nothing)
cv2.createTrackbar("Lighting Filter", "Live Tuning", 51, 99, nothing)
cv2.createTrackbar("Piece Sens (1-10)", "Live Tuning", 3, 10, nothing)
cv2.createTrackbar("Canny Min", "Live Tuning", 30, 255, nothing)
cv2.createTrackbar("Canny Max", "Live Tuning", 35, 255, nothing)

print(f"Connecting to camera {CAMERA_INDEX}...")
cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

print("\n🚀 LIVE AR Piece Detection Started!")
print(" - The system will now draw red dots under detected pieces in real-time.")

# --- STATE VARIABLES ---
files, ranks = ['h','g','f','e','d','c','b','a'], ['1','2','3','4','5','6','7','8']
previous_occupancy = np.zeros((8, 8), dtype=bool)
previous_occupancy[0:2, :] = True
previous_occupancy[6:8, :] = True
previous_squares = {}

def square_to_rc(square_name):
    """Convert 'e4' style square name to (row, col) indices matching the
    files/ranks lists used everywhere else in this script."""
    file_char, rank_char = square_name[0], square_name[1]
    return ranks.index(rank_char), files.index(file_char)

def scan_board(board_img, edges_img, step, sensitivity):
    """Scan the rectified board image and return (occupancy, squares) dicts
    describing exactly what's visible in this one frame -- no comparison to
    memory, just ground truth from the current picture."""
    occupancy = np.zeros((8, 8), dtype=bool)
    squares = {}
    margin = int(step * 0.25)
    for row in range(8):
        for col in range(8):
            sq_name = f"{files[col]}{ranks[row]}"
            y1, y2 = int(row * step), int((row + 1) * step)
            x1, x2 = int(col * step), int((col + 1) * step)
            squares[sq_name] = board_img[y1:y2, x1:x2]
            sq_edges = edges_img[y1 + margin:y2 - margin, x1 + margin:x2 - margin]
            if sq_edges.size > 0 and (np.count_nonzero(sq_edges) / sq_edges.size) > sensitivity:
                occupancy[row][col] = True
    return occupancy, squares

# Castling moves 2 pieces (king + rook) at once, so it shows up as 2 departures
# and 2 arrivals rather than the usual 1-and-1. Map each possible pair of
# vacated squares to the resulting king move + the squares that should now be
# occupied, so it can be recognized as a single move rather than being missed.
CASTLING_MOVES = {
    frozenset(["e1", "h1"]): ("e1", "g1", frozenset(["g1", "f1"])),  # White Kingside
    frozenset(["e1", "a1"]): ("e1", "c1", frozenset(["c1", "d1"])),  # White Queenside
    frozenset(["e8", "h8"]): ("e8", "g8", frozenset(["g8", "f8"])),  # Black Kingside
    frozenset(["e8", "a8"]): ("e8", "c8", frozenset(["c8", "d8"])),  # Black Queenside
}

def apply_move_to_memory(move_info):
    """Update previous_occupancy / previous_squares to reflect a move the
    robot just physically made.

    We know exactly what the robot did (Stockfish told the backend, and the
    backend told us), so there is no need to re-photograph and re-detect the
    board afterwards -- doing that was the source of the old bug, since a
    single post-move camera frame can easily be stale (robot arm still in
    frame, camera buffer lag, etc.) and would silently corrupt board memory.
    """
    global previous_occupancy, previous_squares

    def move_square(frm, to):
        r_from, c_from = square_to_rc(frm)
        r_to, c_to = square_to_rc(to)
        previous_occupancy[r_from][c_from] = False
        previous_occupancy[r_to][c_to] = True
        if frm in previous_squares:
            previous_squares[to] = previous_squares[frm]

    if move_info.get("is_en_passant") and move_info.get("en_passant_victim"):
        r_v, c_v = square_to_rc(move_info["en_passant_victim"])
        previous_occupancy[r_v][c_v] = False

    move_square(move_info["from"], move_info["to"])

    if move_info.get("is_castling") and move_info.get("rook_from"):
        move_square(move_info["rook_from"], move_info["rook_to"])

    print(f"🔄 Board memory updated for robot's move: {move_info['uci']}")

while True:
    ret, frame = cap.read()
    if not ret: break
        
    # Resize first so the offsets are predictable
    img_resized = cv2.resize(frame, (1024, 768))
    
    # --- APPLY CROP OFFSETS ---
    img_cropped = img_resized[OFFSET_TOP:(768 - OFFSET_BOTTOM), OFFSET_LEFT:(1024 - OFFSET_RIGHT)]
    display_frame = img_cropped.copy()
    
    sat_thresh = cv2.getTrackbarPos("Saturation", "Live Tuning")
    min_sq = cv2.getTrackbarPos("Min Sq Size", "Live Tuning")
    max_sq = cv2.getTrackbarPos("Max Sq Size", "Live Tuning")
    adapt_block = cv2.getTrackbarPos("Lighting Filter", "Live Tuning")
    edge_sens = cv2.getTrackbarPos("Piece Sens (1-10)", "Live Tuning") / 100.0
    canny_min = cv2.getTrackbarPos("Canny Min", "Live Tuning")
    canny_max = cv2.getTrackbarPos("Canny Max", "Live Tuning")
    
    if edge_sens <= 0: edge_sens = 0.01
    if adapt_block % 2 == 0: adapt_block += 1
    if adapt_block < 3: adapt_block = 3

    clean_final_board = None

    # --- 1. COARSE PASS ---
    # Feed the cropped image into the pipeline
    hsv = cv2.cvtColor(img_cropped, cv2.COLOR_BGR2HSV)
    _, s, _ = cv2.split(hsv)
    _, mask = cv2.threshold(s, sat_thresh, 255, cv2.THRESH_BINARY)

    kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask_clean = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_small)
    kernel_large = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 40))
    blob_mask = cv2.morphologyEx(mask_clean, cv2.MORPH_CLOSE, kernel_large)
    
    contours, _ = cv2.findContours(blob_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if contours:
        largest_contour = max(contours, key=cv2.contourArea)
        crop_h, crop_w = img_cropped.shape[:2]
        
        # Calculate dynamic threshold based on the cropped image size
        if cv2.contourArea(largest_contour) > (crop_w * crop_h * 0.1):
            rect = cv2.minAreaRect(largest_contour)
            box = cv2.boxPoints(rect)
            box = np.int32(box)
            
            RAW_SIZE = 1000
            ordered_corners = order_points(box)
            dst_pts = np.array([
                [0, 0], [RAW_SIZE - 1, 0], [RAW_SIZE - 1, RAW_SIZE - 1], [0, RAW_SIZE - 1]
            ], dtype="float32")
            
            coarse_matrix = cv2.getPerspectiveTransform(ordered_corners, dst_pts)
            # Warp from the cropped image, not the raw resized image
            warped_raw = cv2.warpPerspective(img_cropped, coarse_matrix, (RAW_SIZE, RAW_SIZE))
            
            # --- 2. FINE PASS ---
            gray_warped = cv2.cvtColor(warped_raw, cv2.COLOR_BGR2GRAY)
            gray_inverted = cv2.bitwise_not(gray_warped)
            
            thresh = cv2.adaptiveThreshold(gray_inverted, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                           cv2.THRESH_BINARY_INV, adapt_block, 10)
            
            inner_contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            
            valid_squares = []
            
            for c in inner_contours:
                area = cv2.contourArea(c)
                if min_sq < area < max_sq:
                    x, y, w, h = cv2.boundingRect(c)
                    aspect_ratio = w / float(h)
                    if 0.75 < aspect_ratio < 1.3:
                        valid_squares.append(c)
                        
            if valid_squares:
                all_pts = np.vstack(valid_squares).reshape(-1, 2)
                
                min_x, max_x = np.min(all_pts[:, 0]), np.max(all_pts[:, 0])
                min_y, max_y = np.min(all_pts[:, 1]), np.max(all_pts[:, 1])
                
                edge_thresh = 30
                top_pts = all_pts[all_pts[:, 1] < min_y + edge_thresh]
                bottom_pts = all_pts[all_pts[:, 1] > max_y - edge_thresh]
                left_pts = all_pts[all_pts[:, 0] < min_x + edge_thresh]
                right_pts = all_pts[all_pts[:, 0] > max_x - edge_thresh]
                
                if len(top_pts) > 1 and len(bottom_pts) > 1 and len(left_pts) > 1 and len(right_pts) > 1:
                    top_line = cv2.fitLine(top_pts, cv2.DIST_L2, 0, 0.01, 0.01)
                    bottom_line = cv2.fitLine(bottom_pts, cv2.DIST_L2, 0, 0.01, 0.01)
                    left_line = cv2.fitLine(left_pts, cv2.DIST_L2, 0, 0.01, 0.01)
                    right_line = cv2.fitLine(right_pts, cv2.DIST_L2, 0, 0.01, 0.01)
                    
                    tl = get_intersection(top_line, left_line)
                    tr = get_intersection(top_line, right_line)
                    bl = get_intersection(bottom_line, left_line)
                    br = get_intersection(bottom_line, right_line)
                    
                    if tl and tr and bl and br:
                        inner_corners = np.array([tl, tr, br, bl], dtype="float32")
                        ordered_inner = order_points(inner_corners)
                        
                        w1 = np.linalg.norm(ordered_inner[0] - ordered_inner[1])
                        h1 = np.linalg.norm(ordered_inner[0] - ordered_inner[3])
                        
                        if w1 > 400 and h1 > 400:
                            dst_final = np.array([
                                [0, 0], [FINAL_SIZE - 1, 0], 
                                [FINAL_SIZE - 1, FINAL_SIZE - 1], [0, FINAL_SIZE - 1]
                            ], dtype="float32")
                            
                            inner_matrix = cv2.getPerspectiveTransform(ordered_inner, dst_final)
                            clean_final_board = cv2.warpPerspective(warped_raw, inner_matrix, (FINAL_SIZE, FINAL_SIZE))
                            
                            # --- 3. THE AR OVERLAY ---
                            combined_matrix = inner_matrix @ coarse_matrix
                            
                            try:
                                inv_combined = np.linalg.inv(combined_matrix)
                            except np.linalg.LinAlgError:
                                continue 
                            
                            grid_points = []
                            step = FINAL_SIZE / 8.0
                            for i in range(9):
                                grid_points.append([i * step, 0])
                                grid_points.append([i * step, FINAL_SIZE])
                                grid_points.append([0, i * step])
                                grid_points.append([FINAL_SIZE, i * step])
                                
                            grid_points = np.array(grid_points, dtype="float32").reshape(-1, 1, 2)
                            transformed_pts = cv2.perspectiveTransform(grid_points, inv_combined)
                            
                            for i in range(0, len(transformed_pts), 2):
                                pt1 = (int(transformed_pts[i][0][0]), int(transformed_pts[i][0][1]))
                                pt2 = (int(transformed_pts[i+1][0][0]), int(transformed_pts[i+1][0][1]))
                                cv2.line(display_frame, pt1, pt2, (0, 255, 0), 2)

                            # --- 4. LIVE PIECE DETECTION ---
                            board_gray = cv2.cvtColor(clean_final_board, cv2.COLOR_BGR2GRAY)
                            board_blur = cv2.GaussianBlur(board_gray, (7, 7), 0)
                            board_edges = cv2.Canny(board_blur, canny_min, canny_max)
                            
                            occupied_centers = []
                            
                            for row in range(8):
                                for col in range(8):
                                    y_start, y_end = int(row * step), int((row + 1) * step)
                                    x_start, x_end = int(col * step), int((col + 1) * step)
                                    
                                    margin = int(step * 0.25)
                                    roi_y_start = y_start + margin
                                    roi_y_end = y_end - margin
                                    roi_x_start = x_start + margin
                                    roi_x_end = x_end - margin
                                    
                                    square_edges = board_edges[roi_y_start:roi_y_end, roi_x_start:roi_x_end]
                                    
                                    if square_edges.size > 0:
                                        density = np.count_nonzero(square_edges) / square_edges.size
                                        if density > edge_sens:
                                            cx = x_start + (step / 2.0)
                                            cy = y_start + (step / 2.0)
                                            occupied_centers.append([cx, cy])
                                        
                            if occupied_centers:
                                centers_ary = np.array(occupied_centers, dtype="float32").reshape(-1, 1, 2)
                                tf_centers = cv2.perspectiveTransform(centers_ary, inv_combined)
                                
                                for pt in tf_centers:
                                    cx_3d, cy_3d = int(pt[0][0]), int(pt[0][1])
                                    cv2.circle(display_frame, (cx_3d, cy_3d), 12, (0, 0, 255), -1)
                                    cv2.circle(display_frame, (cx_3d, cy_3d), 12, (255, 255, 255), 2)

    cv2.imshow("Live Feed - AR Tracking", display_frame)

    key = cv2.waitKey(1) & 0xFF
    if key == 27: 
        break
    elif key == 32: 
        if clean_final_board is not None:
            step = FINAL_SIZE // 8

            # 1. Extract current squares and occupancy
            current_occupancy, current_squares = scan_board(clean_final_board, board_edges, step, edge_sens)

            # 2. Find the departures and arrivals
            departures, arrivals = [], []
            for row in range(8):
                for col in range(8):
                    sq_name = f"{files[col]}{ranks[row]}"
                    if previous_occupancy[row][col] and not current_occupancy[row][col]:
                        departures.append(sq_name)
                    elif not previous_occupancy[row][col] and current_occupancy[row][col]:
                        arrivals.append(sq_name)

            from_sq = None
            to_sq = None
            
            # 3. Calculate Move Type
            if len(departures) == 1 and len(arrivals) == 1:
                from_sq = departures[0]
                to_sq = arrivals[0]
            elif len(departures) == 2 and len(arrivals) == 2:
                # Likely castling: king + rook both moved in the same snapshot.
                castling_match = CASTLING_MOVES.get(frozenset(departures))
                if castling_match:
                    king_from, king_to, expected_arrivals = castling_match
                    if frozenset(arrivals) == expected_arrivals:
                        from_sq, to_sq = king_from, king_to
            elif len(departures) == 1 and len(arrivals) == 0 and previous_squares:
                from_sq = departures[0]
                max_diff = 0
                for row in range(8):
                    for col in range(8):
                        if current_occupancy[row][col]:
                            sq_name = f"{files[col]}{ranks[row]}"
                            diff = cv2.absdiff(previous_squares[sq_name], current_squares[sq_name])
                            score = np.sum(diff)
                            if score > max_diff:
                                max_diff = score
                                to_sq = sq_name

            # 4. Fire the API Request
            if from_sq and to_sq:
                uci_move = from_sq + to_sq
                print(f"\n🚀 Sending Move to Backend: {uci_move}")
                try:
                    res = requests.post('http://127.0.0.1:5050/move', json={'move': uci_move})
                    if res.status_code == 200:
                        print("✅ Backend accepted move! Robot turn initiated.")

                        # 1. Commit what we just saw (our own move) as the new
                        #    ground truth right away -- it was already detected
                        #    reliably from a live frame, no need to wait.
                        previous_occupancy = np.copy(current_occupancy)
                        previous_squares = current_squares.copy()

                        # 2. Apply the robot's move to memory using the exact
                        #    move the backend reports it played. This avoids
                        #    re-scanning the camera after the robot acts, which
                        #    was the source of the "board memory not updated"
                        #    bug (a single post-move frame could be stale/blurry
                        #    while the arm was still mid-motion or in frame).
                        robot_move = res.json().get('robot_move')
                        if robot_move:
                            apply_move_to_memory(robot_move)
                    else:
                        print(f"❌ Illegal move rejected: {res.json()} (guessed {uci_move} from departures={departures}, arrivals={arrivals})")
                        print("   If the board and this guess don't match reality, press 'r' to resync board memory to the current camera frame.")
                except Exception as e:
                    print(f"❌ Server error: {e}")
            else:
                print(f"⚠️ Could not parse move. departures={departures}, arrivals={arrivals}")
                print("   If this doesn't reflect what you actually moved, board memory may already be out of sync -- press 'r' to resync it to the current camera frame, then verify the physical board matches the backend's game state before continuing.")
    elif key == ord('r'):
        if clean_final_board is not None:
            step = FINAL_SIZE // 8
            previous_occupancy, previous_squares = scan_board(clean_final_board, board_edges, step, edge_sens)
            print("🔄 Manual resync: board memory reset to match the current camera frame.")
            print("   Make sure the physical board actually matches the backend's current game state before making your next move.")
        else:
            print("⚠️ Can't resync yet -- board not detected in this frame.")

cap.release()
cv2.destroyAllWindows()