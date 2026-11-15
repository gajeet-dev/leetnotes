import threading
import time

# Two events (flags)
stop_event = threading.Event()
work_event = threading.Event()


def worker():
    print("👷 [Worker] Thread started. Waiting for work...")

    while True:
        # LINE A: The worker is FROZEN here until work_event is True
        work_event.wait()

        # LINE B: When it unfreezes, it checks if it was told to stop
        if stop_event.is_set():
            print("👷 [Worker] I woke up and saw the STOP signal! Going home.")
            break

        # LINE C: If not stopped, it does the work
        print("👷 [Worker] Doing some hard work...")
        time.sleep(1)
        print("👷 [Worker] Work finished! Going back to sleep.")
        work_event.clear()  # reset flag to False so it waits again


# --- MAIN THREAD STARTS HERE ---

# 1. Start the worker thread
t = threading.Thread(target=worker)
t.start()

time.sleep(1)  # Let worker start and freeze at LINE A

# 2. Main thread sends some work
print("\n🧑 [Main] Sending work to worker...")
work_event.set()  # Wakes up the worker!
time.sleep(2)  # Give it time to finish

# 3. Now, Main thread wants to shut down the worker
print("\n🧑 [Main] Telling worker to stop...")
stop_event.set()  # Flag 1: "You should stop"
work_event.set()  # Flag 2: "WAKE UP so you can read Flag 1!"

t.join()  # Wait for worker thread to exit cleanly
print("🧑 [Main] Worker is officially stopped. Program finished!")
