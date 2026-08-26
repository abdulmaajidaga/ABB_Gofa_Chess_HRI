MODULE ChessMotion
    ! Socket Variables
    VAR socketdev server_socket;
    VAR socketdev client_socket;
    VAR string received_cmd;
    
    ! Array to hold [Action, X, Y] sent from Python
    ! Action: 1 = PICK, 2 = PLACE
    VAR num cmd_data{3}; 
    VAR bool is_parsed;

    ! --- TOOL AND WORKOBJECT DEFINITIONS ---
    PERS tooldata Servo:=[TRUE,[[0,0,160],[1,0,0,0]],[0.215,[8.7,12.3,49.2],[1,0,0,0],0.00021,0.00024,0.00009]];
    TASK PERS wobjdata chess_workobj:=[FALSE,TRUE,"",[[0,0,0],[1,0,0,0]],[[-220,989.663,50],[1,0,0,0]]];

    ! --- HOME POSITION ---
    ! Defines a safe parking spot 250mm above the center-ish of the board
    CONST robtarget pHome := [[200,-300,650],[0,0,1,0],[0,0,0,0],[9E+09,9E+09,9E+09,9E+09,9E+09,9E+09]];
    CONST robtarget tHome := [[200,-283.240199578,195.507235305],[0,0,1,0],[0,0,0,0],[9E+09,9E+09,9E+09,9E+09,9E+09,9E+09]];
    CONST robtarget tHomeZOffset := [[199.999965031,-100.000072343,103.135393127],[-0,0,1,-0.000000014],[1,-1,1,0],[9E+09,9E+09,9E+09,9E+09,9E+09,9E+09]];

    PROC main()
        ! 1. SAFE STARTUP SEQUENCE
!        SetDO DO_Gripper, 0;  ! Ensure gripper is physically OPEN at startup
        PulseDO\PLength:=0.2,DO16_GripperOpen;
        WaitTime 0.5;
        
        ! Move safely to the Home position before accepting any commands
        ! Using MoveJ (Joint move) to avoid singularity/linear path errors from unknown start poses
        MoveJ pHome, v200, z10, Servo \WObj:=chess_workobj;
        MoveL tHome, v200, z10, Servo \WObj:=chess_workobj;
        MoveL tHomeZOffset, v200, fine, Servo \WObj:=chess_workobj;
        
        ! 2. START SERVER
        SocketCreate server_socket;
        SocketBind server_socket, "127.0.0.1", 5000;
        SocketListen server_socket;
        
        WHILE TRUE DO
            TPWrite "Waiting for Python on port 5000...";
            SocketAccept server_socket, client_socket;
            HandleConnection;
            SocketClose client_socket;
        ENDWHILE
    ENDPROC

    PROC HandleConnection()
        WHILE TRUE DO
            ! Added \Time:=WAIT_MAX so the robot waits forever while you think
            SocketReceive client_socket \Str:=received_cmd \Time:=WAIT_MAX;
            
            ! Instantly convert string "[1, 200, 150]" into the cmd_data array
            is_parsed := StrToVal(received_cmd, cmd_data);
            
            IF is_parsed THEN
                ! Call our dynamic macro using the parsed X (cmd_data{2}) and Y (cmd_data{3})
                ExecuteMove cmd_data{1}, cmd_data{2}, cmd_data{3};
                SocketSend client_socket \Str:="DONE";
            ELSE
                TPWrite "Error: Bad format -> " + received_cmd;
                SocketSend client_socket \Str:="ERROR";
            ENDIF
        ENDWHILE
    ERROR
        ! If a timeout error still somehow occurs, just ignore it and wait again
        IF ERRNO = ERR_SOCK_TIMEOUT THEN
            RETRY;
        ENDIF
        
        TPWrite "Python disconnected.";
        
        ! Park safely back at home when the game/script ends
        ! Using MoveL to safely back out of the camera's L-bracket structure linearly
        MoveL tHomeZOffset, v200, z10, Servo \WObj:=chess_workobj;
        MoveL tHome, v200, z10, Servo \WObj:=chess_workobj;
        MoveL pHome, v200, z10, Servo \WObj:=chess_workobj;
        
        RETURN;
    ENDPROC

    ! --- THE DYNAMIC PICK AND PLACE MACRO ---
    PROC ExecuteMove(num action, num target_x, num target_y)
        ! 1. ALL variables must be declared at the very top
        VAR robtarget base_target;
        VAR num hover_height := 100; ! Hover 100mm above the piece
        
        ! --- Z OFFSET CONTROL ---
        ! Change this value to adjust pickup height. 
        ! Positive = Higher up, Negative = Deeper down.
        VAR num z_offset := 25; 
        
        ! 2. NOW executable statements can begin
        base_target := [[target_x, target_y, z_offset], [0, 0, 1, 0], [0, 0, 0, 0], [9E9, 9E9, 9E9, 9E9, 9E9, 9E9]];
        
        ! 3. Approach position (Hover)
        MoveL Offs(base_target, 0, 0, hover_height), v200, z10, Servo \WObj:=chess_workobj;
        
        ! 4. Move down to the piece
        MoveL base_target, v50, fine, Servo \WObj:=chess_workobj;
        
        ! 5. Actuate Gripper
        IF action = 1 THEN
!            SetDO DO_Gripper, 1; ! Close gripper (PICK)
            PulseDO\PLength:=0.2,DO16_GripperClose;
            WaitTime 0.5;
        ELSEIF action = 2 THEN
!            SetDO DO_Gripper, 0; ! Open gripper (PLACE)
            PulseDO\PLength:=0.2,DO16_GripperOpen;

            WaitTime 0.5;
        ENDIF
        
        ! 6. Retract safely back up to hover height
        MoveL Offs(base_target, 0, 0, hover_height), v200, z10, Servo \WObj:=chess_workobj;

        ! 7. Return to the intermediate home position ONLY after a PLACE action
        IF action = 2 THEN
            MoveL tHomeZOffset, v200, z10, Servo \WObj:=chess_workobj;
        ENDIF
    ENDPROC
    
    PROC CalibTest()
        TPWrite "--- Starting Calibration Dry Run ---";
        
        ! 1. SAFE STARTUP APPROACH
!        SetDO DO_Gripper, 0; ! Ensure gripper is open
        PulseDO\PLength:=0.2,DO16_GripperOpen;
        WaitTime 0.5;
        MoveJ pHome, v200, z10, Servo \WObj:=chess_workobj;
        MoveL tHome, v200, z10, Servo \WObj:=chess_workobj;
        MoveL tHomeZOffset, v200, fine, Servo \WObj:=chess_workobj;
        
        ! 2. TEST POINTS (Action 0 = No gripper actuation)
        ! Test Near Corner (approx A1/H1)
        TPWrite "Testing Corner 1 (21, 21)...";
        ExecuteMove 0, 21, 21;
        
        ! Test Far Corner (approx H8/A8)
        TPWrite "Testing Corner 2 (315, 315)...";
        ExecuteMove 0, 315, 315;
        
        ! Test Center of Board
        TPWrite "Testing Center (168, 168)...";
        ExecuteMove 0, 168, 168;

        TPWrite "Calibration Complete. Retracting safely...";
        
        ! 3. SAFE LINEAR RETRACTION (Camera Avoidance)
        MoveL tHomeZOffset, v200, z10, Servo \WObj:=chess_workobj;
        MoveL tHome, v200, z10, Servo \WObj:=chess_workobj;
        MoveL pHome, v200, fine, Servo \WObj:=chess_workobj;
        
        TPWrite "--- Routine Finished ---";
    ENDPROC

ENDMODULE