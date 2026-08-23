# App-layer IDF component (drivetrain-core task 1.1: minimal skeleton only,
# no ports wired up here). Depends on the drivetrain_control component to
# prove the app layer can link against the pure-logic library.
idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES drivetrain_control
)
